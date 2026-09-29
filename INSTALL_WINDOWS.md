# Parts Manager — автономный Windows Installer

## Что получает покупатель

После установки Parts Manager запускается из своего встроенного CPython Runtime.
Отдельно устанавливать Python, pip или зависимости не требуется.

- программа: `C:\Program Files\Parts Manager`;
- встроенный Python: `C:\Program Files\Parts Manager\runtime`;
- данные пользователя: `%LOCALAPPDATA%\Parts Manager\data`;
- ярлык в меню Пуск;
- опциональный ярлык на рабочем столе;
- браузер открывается автоматически;
- порт 8000 используется первым, затем выбирается следующий свободный localhost-порт.

Удаление программы не удаляет пользовательские данные.

## Сборка коммерческого Setup.exe

Нужен Windows PC с Inno Setup 6.x.

1. Скачать официальный **CPython Windows embeddable package (64-bit)** для Python 3.13.x.
2. Распаковать его содержимое непосредственно в папку `runtime\` этого проекта.
3. Проверить наличие:
   - `runtime\python.exe`
   - `runtime\pythonw.exe`
   - остальных файлов embeddable distribution.
4. Запустить `build_windows.bat`.
5. Получить `installer_output\PartsManager-Setup-11.0.5.exe`.

Проект не использует pip-зависимости приложения: основной код работает на стандартной библиотеке Python.

### Важно

В этой поставке самого Windows Runtime нет, потому что текущая среда сборки Linux не может получить и упаковать Windows бинарники. После добавления официального runtime итоговый Inno Setup будет самодостаточным для клиента.
