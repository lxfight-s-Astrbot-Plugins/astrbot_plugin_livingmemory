<p align="center">
  <a href="README.md">中文</a> · <a href="README_en.md">English</a> · <strong>Русский</strong>
</p>

![LivingMemory: долговременная память, связывающая предпочтения, людей, планы и контекст](docs/public/images/livingmemory-cover.svg)

<p align="center">
  <a href="https://lxfight-s-astrbot-plugins.github.io/astrbot_plugin_livingmemory/en/"><strong>Документация · EN</strong></a> ·
  <a href="https://lxfight-s-astrbot-plugins.github.io/astrbot_plugin_livingmemory/en/webui"><strong>Панель управления</strong></a> ·
  <a href="https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases"><strong>Скачать</strong></a> ·
  <a href="https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/issues"><strong>Обратная связь</strong></a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-52675c?style=flat-square" alt="Python 3.10 или новее">
  <img src="https://img.shields.io/badge/AstrBot-4.24.2%2B-52675c?style=flat-square" alt="Для Pages требуется AstrBot 4.24.2 или новее">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-AGPL--3.0-52675c?style=flat-square" alt="AGPL-3.0"></a>
</p>

## Сохранить важное из разговора

LivingMemory хранит для AstrBot предпочтения, отношения, ход проектов и прежние договорённости. Плагин создаёт сводки диалогов, находит нужный контекст с помощью ключевых слов, векторов и графа, а также управляет памятью через архивирование, затухание и усиление при обращении.

> **Тестовая версия: 2.7.0-beta.1**<br>
> Четыре стиля панели, стабильные подписи графа и понятные сценарии редактирования и импорта.<br>
> [Скачать бета-версию](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases/tag/2.7.0-beta.1) · [Обновление · EN](https://lxfight-s-astrbot-plugins.github.io/astrbot_plugin_livingmemory/en/releases) · [Стабильная 2.6.1](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases/tag/2.6.1)

## От сохранения к воспоминанию

- **Автоматическая запись** — Сводки создаются после заданного числа реплик. Важные записи могут сохранять исходные сообщения для проверки и повторного обобщения.
- **Поиск с учётом контекста** — Документы и граф поддерживают поиск по ключевым словам и векторам, объединение рейтингов, фильтрацию области и проверку жизненного цикла.
- **Инструменты агента** — `recall_long_term_memory` и `memorize_long_term_memory` позволяют читать и записывать долговременную память по мере необходимости.
- **Управление через Pages** — Просмотр связей, редактирование памяти, тестирование поиска, настройка промптов и состояние системы.

## Одно пространство, четыре стиля

**Editorial** — зелёная сетка и чёткие линии. **Studio** — мягкие карточки. **Paper** — тёплые поверхности и заголовки с засечками. **Terminal** — моноширинный шрифт и приборная компоновка.

Каждый стиль поддерживает светлый, тёмный и автоматический режимы. Переключение доступно в настройках внешнего вида; выбор сохраняется в текущем браузере. При увеличении графа подписи остаются стабильными, а на телефоне доступны фильтр диалога и переход к записи.

[Темы и работа с панелью · EN →](https://lxfight-s-astrbot-plugins.github.io/astrbot_plugin_livingmemory/en/webui)

## Начало работы

1. Установите плагин из каталога AstrBot или поместите выбранную версию в `data/plugins/astrbot_plugin_livingmemory`.
2. Перезагрузите AstrBot и выберите провайдеры Embedding и LLM в настройках LivingMemory; пустые поля используют настройки AstrBot.
3. Откройте `Plugins → LivingMemory → Pages → dashboard`. Для Pages требуется **AstrBot 4.24.2 или новее**.

Для тестирования скачайте указанный выше тег и следуйте [инструкции по установке и откату · EN](https://lxfight-s-astrbot-plugins.github.io/astrbot_plugin_livingmemory/en/releases). Перед обновлением сохраните резервную копию данных и конфигурации плагина.

После нескольких реплик проверьте `/lmem status`, `/lmem summarize` и `/lmem search ключевые слова`.

## Подробнее

[Быстрый старт · EN](https://lxfight-s-astrbot-plugins.github.io/astrbot_plugin_livingmemory/en/guide/getting-started) · [Настройки · EN](https://lxfight-s-astrbot-plugins.github.io/astrbot_plugin_livingmemory/en/configuration) · [Команды · EN](https://lxfight-s-astrbot-plugins.github.io/astrbot_plugin_livingmemory/en/commands) · [Архитектура · EN](https://lxfight-s-astrbot-plugins.github.io/astrbot_plugin_livingmemory/en/architecture) · [История изменений](CHANGELOG.md)

При обновлении с v1.4.0–v1.4.2 сначала прочитайте [инструкцию по миграции · EN](https://lxfight-s-astrbot-plugins.github.io/astrbot_plugin_livingmemory/en/configuration#backup-migration-and-cleanup).

---

Сообщество: [Группа QQ 953245617](https://qm.qq.com/cgi-bin/qm/qr?k=WdyqoP-AOEXqGAN08lOFfVSguF2EmBeO&jump_from=webapi&authKey=tPyfv90TVYSGVhbAhsAZCcSBotJuTTLf03wnn7/lQZPUkWfoQ/J8e9nkAipkOzwh) · Пароль: `lxfight`<br>
Лицензия: [AGPL-3.0](LICENSE)
