# Компоненты E Voice

Авторский код E Voice распространяется на условиях MIT. В комплекте есть его файл `LICENSE`, папка `licenses` с уведомлениями зависимостей и отдельная лицензия модели в `models/gigaam-v3/LICENSE`. В Linux эти ресурсы находятся в `_internal`, на Mac — в `E Voice.app/Contents/Resources`. [Лицензия E Voice](LICENSE)

| Компонент | Источник и уведомления |
| --- | --- |
| GigaAM v3, русская речь | GigaChat Team, MIT. [Авторы](https://github.com/salute-developers/GigaAM) · [ONNX-экспорт и файл лицензии](https://huggingface.co/csukuangfj/sherpa-onnx-nemo-transducer-punct-giga-am-v3-russian-2025-12-16/tree/a6039be7cee829a9044a69ac0ebaf1c191217c97) |
| Публичный звуковой пример в тестах | Файл `test_wavs/example.wav` из той же ревизии модели. [Источник](https://huggingface.co/csukuangfj/sherpa-onnx-nemo-transducer-punct-giga-am-v3-russian-2025-12-16/tree/a6039be7cee829a9044a69ac0ebaf1c191217c97/test_wavs) |
| sherpa-onnx | Движок распознавания, Apache-2.0. [Исходники](https://github.com/k2-fsa/sherpa-onnx) |
| ONNX Runtime | Microsoft, MIT. В папке `licenses` приложены лицензия и уведомления о компонентах выпуска 1.28.2. [Исходники](https://github.com/microsoft/onnxruntime/tree/v1.28.2) |
| Python, Tcl/Tk | Интерпретатор и графическая библиотека. [Python](https://www.python.org/downloads/) · [Tcl/Tk](https://www.tcl-lang.org/software/tcltk/license.html) |
| PortAudio, python-sounddevice | Захват звука. [PortAudio](https://www.portaudio.com/license.html) · [python-sounddevice](https://github.com/spatialaudio/python-sounddevice) |
| Pillow | Отрисовка плавающей кнопки, краёв стекла и значка в области уведомлений. [Pillow](https://github.com/python-pillow/Pillow) |
| pywin32, pystray, cffi, six | Доступ к Win32, значок области уведомлений и системные привязки. [pywin32](https://github.com/mhammond/pywin32) · [pystray](https://github.com/moses-palmer/pystray) · [cffi](https://github.com/python-cffi/cffi) · [six](https://github.com/benjaminp/six) |
| pynput, python-xlib, PyObjC | Сочетания клавиш, события мыши и взаимодействие с активным полем macOS/X11. [pynput 1.8.2](https://pypi.org/project/pynput/1.8.2/#files) · [python-xlib 0.33](https://pypi.org/project/python-xlib/0.33/#files) · [PyObjC](https://github.com/ronaldoussoren/pyobjc) |
| PyInstaller | Упаковка программы. [Лицензия и исключение для загрузчика](https://pyinstaller.org/en/stable/license.html) |
| Inno Setup | Создание установщика. [Inno Setup](https://jrsoftware.org/isinfo.php) |

В нативных сборках модули pystray, pynput и python-xlib соответствующей платформы поставляются отдельными файлами Python. Их можно изучать, менять и заменять. Публичный репозиторий содержит спецификации PyInstaller и инструкции повторной сборки. Для Mac после изменения содержимого пакета приложения может понадобиться новая подпись. [pystray 0.19.5](https://github.com/moses-palmer/pystray/tree/v0.19.5) · [Сборка E Voice](README.md#сборка-и-проверка)

Для передачи копий 1.1.5 сохраните уведомления из установщика и предоставьте соответствующие исходники каждому получателю. Отдельный архив содержит платформенные лицензии, проверенные перечни файлов, авторский код этого выпуска и исходники зависимостей, включая точные исходные пакеты Ubuntu и обе версии ALSA. Модули под LGPL можно заменять и отлаживать. [Исходники и лицензии 1.1.5](https://github.com/moldset-mmc/e-voice/releases/download/v1.1.5/E-Voice-1.1.5-Licenses-and-Sources.zip) · [Условия для распространителя](https://github.com/moldset-mmc/e-voice/tree/v1.1.5#бесплатное-и-платное-распространение)

Windows использует две неизменённые библиотеки Microsoft Visual C++ 2022 x64 из REDIST. Имена, версии, подписи и SHA-256 записаны в `licenses/Microsoft-runtime-files.json`. Условия их установки и дальнейшей передачи приведены в `licenses/windows-runtime-terms.txt` и распространяются только на код Microsoft Distributable Code. В Windows и macOS используются штатные звуковые интерфейсы PortAudio. ASIO DLL исключены. [Перечень REDIST Microsoft](https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution) · [Условия Windows-сборки CPython](https://github.com/python/cpython/blob/v3.14.10/PC/crtlicense.txt)
