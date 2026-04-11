import os
import tempfile
import pytest
from pathlib import Path

# Импортируем вашу логику истории
import core.history
from core.history import HistoryManager, HistoryRecord

@pytest.fixture
def temp_history_manager():
    """Эта фикстура создает и настраивает 'чистую' тестовую базу данных."""
    # 1. Создаем временный файл
    fd, temp_db_path = tempfile.mkstemp(suffix='.db')
    
    # 2. Временно "подменяем" константу DB_PATH в коде
    original_db_path = core.history.DB_PATH
    core.history.DB_PATH = Path(temp_db_path)
    
    # 3. Инициализируем чистый менеджер истории
    manager = HistoryManager()
    
    # Отдаём менеджер тесту
    yield manager
    
    # 4. После завершения теста чистим за собой
    try:
        manager._get_conn().close()
    except Exception:
        pass
    os.close(fd)
    try:
        os.remove(temp_db_path)
    except PermissionError:
        pass # Игнорируем ошибку занятого файла в Windows
        
    # Возвращаем оригинальный путь
    core.history.DB_PATH = original_db_path

def test_add_and_get_record(temp_history_manager):
    """Тест: Добавление записи и проверка ее сохранения."""
    # Добавляем фейковую загрузку
    record = temp_history_manager.add_record(
        url="https://youtube.com/watch?v=123",
        title="Мое тестовое видео",
        thumbnail="img.jpg",
        file_path="C:/videos/test.mp4",
        file_size=1024,
        format="mp4",
        quality="1080p",
        status="completed"
    )
    
    # Проверяем, что вернулся правильный объект
    assert isinstance(record, HistoryRecord)
    assert record.title == "Мое тестовое видео"
    
    # Достаём все из базы и убеждаемся, что там 1 запись
    all_records = temp_history_manager.get_all()
    assert len(all_records) == 1
    assert all_records[0].url == "https://youtube.com/watch?v=123"

def test_delete_record(temp_history_manager):
    """Тест: Удаление записи из истории."""
    record = temp_history_manager.add_record(
        url="https://youtube.com/watch?v=456",
        title="Видео для удаления",
        thumbnail="img.jpg",
        file_path="C:/videos/del.mp4",
        file_size=512,
        format="mp4",
        quality="720p",
        status="completed"
    )
    
    assert len(temp_history_manager.get_all()) == 1
    
    # Удаляем и проверяем, что удаление вернуло True
    success = temp_history_manager.delete_record(record.id)
    assert success is True
    
    # Проверяем, что база теперь пуста
    assert len(temp_history_manager.get_all()) == 0

def test_clear_all_records(temp_history_manager):
    """Тест: Очистка всей истории загрузок."""
    temp_history_manager.add_record("url1", "Video 1", "", "", 0, "", "", "completed")
    temp_history_manager.add_record("url2", "Video 2", "", "", 0, "", "", "completed")
    
    assert len(temp_history_manager.get_all()) == 2
    
    # Очищаем все
    temp_history_manager.clear_all()
    
    assert len(temp_history_manager.get_all()) == 0
