-- Создание таблицы справочника владельцев
CREATE TABLE owners (
    id SERIAL PRIMARY KEY,
    owner_name VARCHAR(100) NOT NULL UNIQUE
);

-- Создание таблицы событий
CREATE TABLE events (
    id SERIAL PRIMARY KEY,
    event_name VARCHAR(200) NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    owner_name VARCHAR(100) NOT NULL,

    -- Внешний ключ для связи с таблицей owners
    CONSTRAINT fk_events_owners
        FOREIGN KEY (owner_name)
        REFERENCES owners(owner_name)
        ON DELETE RESTRICT
        ON UPDATE CASCADE
);

-- Создание индексов для оптимизации запросов
CREATE INDEX idx_events_timestamp ON events(timestamp);
CREATE INDEX idx_events_owner_name ON events(owner_name);
CREATE INDEX idx_owners_name ON owners(owner_name);

-- Комментарии к таблицам и полям
COMMENT ON TABLE owners IS 'Справочник владельцев событий';
COMMENT ON COLUMN owners.id IS 'Уникальный идентификатор владельца';
COMMENT ON COLUMN owners.owner_name IS 'Имя владельца (уникальное)';

COMMENT ON TABLE events IS 'Таблица событий';
COMMENT ON COLUMN events.id IS 'Уникальный идентификатор события';
COMMENT ON COLUMN events.event_name IS 'Название события';
COMMENT ON COLUMN events.timestamp IS 'Временная метка события';
COMMENT ON COLUMN events.owner_name IS 'Имя владельца события (ссылка на owners.owner_name)';

-- Добавление владельцев
INSERT INTO owners (owner_name) VALUES
    ('Иван Петров'),
    ('Мария Сидорова'),
    ('Алексей Козлов');

-- Добавление событий
INSERT INTO events (event_name, owner_name) VALUES
    ('Встреча с клиентом', 'Иван Петров'),
    ('Презентация проекта', 'Мария Сидорова');
