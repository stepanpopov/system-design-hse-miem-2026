# HW2 — Отказоустойчивый кластер PostgreSQL (Patroni + etcd + HAProxy)

## 0. Окружение запуска

Стек поднимается из директории `code/postgres-ha`: сначала собирается образ
`patroni`, затем поднимается весь кластер одной командой. Запуск выполнялся под
**Podman** (вместо Docker), поэтому команды задания читаются как
`podman` / `podman-compose` вместо `docker` / `docker compose` — на логику работы
кластера это не влияет.

```bash
podman build -t patroni code/postgres-ha/patroni-master
podman-compose -f code/postgres-ha/docker-compose.yml up -d
```

---

## Этап №1. Архитектура и запуск кластера

### 1.1 Состав кластера

`docker-compose.yml` поднимает 11 контейнеров, которые делятся на четыре группы:

| Группа | Контейнеры | Назначение |
|---|---|---|
| DCS | `demo-etcd1/2/3` | Distributed Configuration Store на Raft; хранит состояние кластера, требует кворума |
| База данных | `demo-patroni1/2/3` | PostgreSQL под управлением Patroni (1 мастер + 2 реплики) |
| Балансировщик | `demo-haproxy` | единая точка входа; `5002→master`, `5001→replicas`, `7001→stats` |
| Мониторинг | `prometheus`, `grafana`, `postgres_exporter` | сбор метрик и готовые дашборды |

Путь запроса от приложения до базы:

```bash
python3 -m venv .venv && .venv/bin/pip install psycopg2-binary
```

### 1.3 Состояние кластера (`patronictl list`)

```
$ podman exec demo-patroni1 patronictl list
+ Cluster: demo (7653232104426700819) -------+----+-------------+-----+------------+-----+
| Member   | Host      | Role    | State     | TL | Receive LSN | Lag | Replay LSN | Lag |
+----------+-----------+---------+-----------+----+-------------+-----+------------+-----+
| patroni1 | 10.89.1.6 | Leader  | running   |  1 |             |     |            |     |
| patroni2 | 10.89.1.7 | Replica | streaming |  1 |   0/4093E40 |   0 |  0/4093E40 |   0 |
| patroni3 | 10.89.1.8 | Replica | streaming |  1 |   0/4093E40 |   0 |  0/4093E40 |   0 |
+----------+-----------+---------+-----------+----+-------------+-----+------------+-----+
```

Поля:

- **Cluster: demo** — scope кластера; под этим ключом всё хранится в etcd
  (`/service/demo/...`).
- **Leader / running** — текущий мастер (`patroni1`), удерживающий leader-key в
  DCS; на него идёт запись.
- **Replica / streaming** — реплики (`patroni2`, `patroni3`), тянущие WAL через
  streaming-репликацию.
- **TL (Timeline)** — номер ветки истории PostgreSQL; увеличивается при каждом
  failover-промоушене (далее в отчёте наблюдается переход 1 → 2 → 3).
- **Lag = 0** — реплики догнаны до мастера.
- **Receive / Replay LSN** — позиция в WAL, до которой реплика приняла и применила
  данные.

Реализована классическая схема **1 мастер + 2 реплики**, координируемая
через etcd; роль лидера определяется наличием leader-key в DCS, а не статической
конфигурацией.

---

## Этап №2. HAProxy — маршрутизация трафика

Состояние пулов на странице статистики `http://localhost:7001/`:

```
primary   patroni1  UP      <- лидер
primary   patroni2  DOWN
primary   patroni3  DOWN
replicas  patroni1  DOWN
replicas  patroni2  UP
replicas  patroni3  UP
```

HAProxy не знает заранее, кто мастер, и определяет это http-проверкой Patroni REST
API:

- пул `primary` — `option httpchk HEAD /primary`: статус 200 отдаёт только
  текущий лидер;
- пул `replicas` — `HEAD /replica`: статус 200 отдают только реплики.

---

## Этап №3. Данные и нагрузка

### 3.1 Схема и проверка репликации

```bash
podman exec -i demo-patroni1 psql -U postgres -d postgres < code/postgres-ha/schema.sql
```

Проверка, что данные реплицировались и что реплика доступна только на чтение:

```
$ podman exec demo-patroni2 psql -U postgres -d postgres -c "SELECT * FROM owners ORDER BY id;"
 id |   owner_name
----+----------------
  1 | Иван Петров
  2 | Мария Сидорова
  3 | Алексей Козлов

$ ... -c "INSERT INTO events(...) VALUES(...);"   # на реплике
ERROR:  cannot execute INSERT in a read-only transaction
```

Репликация работает, реплика честно read-only — запись физически возможна
только на мастере.

### 3.2 Генератор трафика

```
$ .venv/bin/python traffic-generator.py
--- STARTING LOAD GENERATOR ON PORT 5002 ---
[01:46:41] CONNECTED to Master Node
[01:46:41] INSERT: login by Иван Петров
READ check (Last 3 IDs): [124, 114, 113]
[01:46:42] INSERT: purchase by Иван Петров
```

Генератор подключается к `localhost:5002` с `target_session_attrs=read-write`,
гарантированно попадая на мастер. И запись (`INSERT`), и чтение (`SELECT`) идут
**с мастера**: реплики в этом скрипте напрямую под нагрузку чтения не попадают и
служат горячим резервом для failover. Для реального чтения с реплик приложение
должно обращаться к порту `5001` (пул `replicas`). Монотонно растущие ID
подтверждают, что записи проходят и коммитятся.

---

## Этап №4. Отказоустойчивость

Все эксперименты выполнены без остановки генератора; одновременно
отслеживались `patronictl list`, лог приложения и состояние HAProxy.

### Эксперимент 1. Отказ лидера (`patroni1`)

```
>>> 01:45:06 stopping LEADER patroni1
| patroni1 | Replica | stopped   |    |
| patroni2 | Replica | streaming |  2 |
| patroni3 | Leader  | running   |  2 |   <- новый лидер, TL 1 -> 2
```

Лог приложения в этот момент:

```
[01:45:07] CONNECTION LOST (Failover in progress?): server closed the connection unexpectedly
[01:46:41] CONNECTED to Master Node      <- переподключение на нового лидера
```

Последовательность событий: Patroni через etcd зафиксировал пропажу лидера и
выбрал нового (`patroni3`); timeline увеличился 1 → 2; HAProxy перестроил пул
`primary` и оборвал старые сессии; приложение по своей логике
(`except OperationalError → conn=None`) переподключилось и продолжило запись.

### Эксперимент 2. Возврат бывшего лидера

```
>>> starting patroni1 back
| patroni1 | Replica | streaming |  3 |   <- вернулся как РЕПЛИКА
| patroni2 | Replica | streaming |  3 |
| patroni3 | Leader  | running   |  3 |
```

Бывший лидер не забирает лидерство обратно автоматически, а поднимается как
реплика и догоняет кластер. Это исключает лишние штормы переключения.

### Эксперимент 3. Отказ реплики (`patroni2`)

```
>>> events before: 163
>>> stopping REPLICA patroni2
| patroni2 | Replica | stopped   |    |
>>> events after:  174   (delta = +11)
```

В логе приложения за время эксперимента нет ни одного `CONNECTION LOST`, счётчик
событий вырос без пауз.

Падение реплики приложение не замечает — реплика не находится на
критическом пути записи. Теряется только избыточность (запас по HA) и потенциальная
мощность чтения, доступность сервиса не страдает.

### Эксперимент 4. Отказ одной ноды etcd (кворум 2/3 сохранён)

```
>>> stopping ONE etcd node (etcd3)
$ etcdctl endpoint health
http://etcd1:2379 is healthy
http://etcd2:2379 is healthy
http://etcd3:2379 is unhealthy: context deadline exceeded

| patroni1 | Replica | streaming |  2 |
| patroni2 | Replica | streaming |  2 |
| patroni3 | Leader  | running   |  2 |
# приложение пишет дальше (IDs 214 -> 218)
```

При потере одной etcd-ноды кворум Raft (2 из 3) сохраняется, DCS остаётся
доступен, Patroni и приложение работают штатно. В логах присутствуют только
предупреждения о недоступной ноде.

### Эксперимент 5. Потеря кворума etcd (1/3) — ключевой кейс

```
>>> 01:48:28 stopping SECOND etcd node (etcd2) -> quorum LOST

$ patronictl list
... etcd.EtcdConnectionFailed: No more machines in the cluster
... patroni.dcs.etcd3.Etcd3Error: Etcd is not responding properly

# лог приложения:
[01:48:59] Connection failed: ... port 5002 failed: server closed the connection unexpectedly
[01:49:00] Connection failed: ... port 5002 failed: server closed the connection unexpectedly
```

При одном живом узле из трёх etcd теряет кворум и становится недоступен на запись.
Patroni не может продлить leader-key и по истечении TTL демотирует мастера в
read-only. Поскольку лидера-`primary` больше нет, healthcheck `/primary` ни у кого
не отдаёт 200, в пуле `primary` HAProxy не остаётся живых серверов, и приложение
получает обрыв на каждом подключении.

Запись намеренно останавливается ради защиты от split-brain — система
выбирает консистентность в ущерб доступности. Даже
`patronictl` не может прочитать состояние, пока кворум не восстановлен.

### Эксперимент 6. Восстановление кворума etcd

```
>>> restoring etcd2 and etcd3
| patroni1 | Replica | streaming |  3 |
| patroni2 | Replica | streaming |  3 |
| patroni3 | Leader  | running   |  3 |   <- TL 2 -> 3 (повторная промоция)
# приложение восстановилось (IDs 258 -> 262)
```

После возврата кворума Patroni подтверждает лидера, HAProxy снова видит
`primary`, приложение самостоятельно переподключается — кластер восстанавливается
без ручного вмешательства.

### Эксперимент 7. Отказ HAProxy (единая точка отказа)

```
>>> 01:49:49 stopping HAProxy

# БД-кластер при этом полностью здоров:
| patroni1 | Replica | streaming |  3 |
| patroni2 | Replica | streaming |  3 |
| patroni3 | Leader  | running   |  3 |

# но приложение не может достучаться до него:
[01:49:58] Connection failed: ... port 5002 failed: Connection refused
```

---

## Ответы на вопросы

**Продолжает ли приложение работать?** Да, при отказе любого одного компонента,
имеющего резерв (любая нода Patroni; одна нода etcd) — приложение продолжает
работать, в худшем случае с паузой в несколько секунд на переподключение. Не
переживаются только потеря кворума etcd и отказ единственного HAProxy.

**Что меняется при выключении/включении ноды Patroni?**

- Отказ лидера - автоматический failover: реплика становится мастером,
  TL +1, приложение коротко теряет соединение и переподключается.
- Отказ реплики - для приложения ничего не меняется, теряется только
  избыточность.
- Возврат ноды - она входит как реплика и догоняет WAL; бывший лидер
  лидерство не возвращает.

**Что меняется при выключении/включении ноды etcd? Идут ли чтение/запись?**

- Минус одна нода (кворум 2/3) - всё работает, в логах только предупреждения.
- Минус две ноды (кворум потерян) - мастер демотируется в read-only, запись
  останавливается; через HAProxy `primary` обрываются и подключения. Это защита
  от split-brain.
- Возврат кворума → кластер восстанавливается самостоятельно.

**Что если выключить HAProxy? Достаточна ли отказоустойчивость? Как избежать в
проде?** Приложение полностью теряет доступ к здоровому кластеру, то есть
отказоустойчивости недостаточно — HAProxy здесь SPOF. Способы устранить в
продакшене:

- несколько инстансов HAProxy + плавающий Virtual IP через **Keepalived (VRRP)**:
  при отказе активного балансировщика VIP переезжает на резервный;
- балансировщик уровня инфраструктуры/облака (AWS NLB, GCP LB, k8s
  Service/MetalLB), избыточный по своей природе;
- **client-side failover** в строке подключения
  (`host=patroni1,patroni2,patroni3 ... target_session_attrs=read-write` — libpq
  сам находит мастер), что устраняет единственную точку входа;
- на практике подходы комбинируют: пара HAProxy + Keepalived, опрашивающая Patroni
  REST API.

---

## Этап №6. Мониторинг (Grafana)

Дашборды из `grafana_dashboards/*.json` автоматически не подхватываются (в
`docker-compose.yml` у Grafana нет provisioning-маунтов), поэтому импортировал их
через Grafana HTTP API через скрипт `import_dashboards.py`:

Результат импорта:

```
- Postgres Overview    -> http://localhost:3000/d/wGgaPlciz/postgres-overview
- PostgreSQL Database  -> http://localhost:3000/d/000000039/postgresql-database
- PostgreSQL Patroni   -> http://localhost:3000/d/rLzu8z_Vk/postgresql-patroni
```

Проверка, что метрики поступают:

| Метрика | Значение | Что показывает |
|---|---|---|
| `patroni_postgres_running` | 3 series, =1 | все ноды живы |
| `patroni_primary` | 3 series | какая нода является лидером |
| `patroni_cluster_unlocked` | =0 | у кластера есть лидер (здоров) |
| `pg_up` | 1 | `postgres_exporter` работает |
| `pg_stat_database_xact_commit` | 3 series | транзакционная активность |

Prometheus собирает четыре таргета (`p1/p2/p3` — Patroni REST `:8008/metrics`,
`postgres` — `postgres_exporter:9187`), все в состоянии `up`. На дашборде
PostgreSQL Patroni видно переключение роли лидера между нодами во время
экспериментов;  
Postgres Overview / Database показывают TPS, число соединений,
commit/rollback. Доступ: `http://localhost:3000` (admin/admin).
