"""The apps of the *Arr page: each in its own folder.

    arr/
    ├── registry.py   the registry of apps - the only place that knows them all
    ├── sonarr/       series: history, queue, calendar, live downloads
    ├── radarr/       movies: the same for Radarr
    ├── bazarr/       subtitles
    └── prowlarr/     indexers

The rules of the tree (guarded by tests/test_arr_tree.py):

* Everything that knows anything about an app is in its files: the package here, the statistics
  `stats/<app>.py`, the cards `cards_<app>.py`, the templates `_card_<app>_*.html` and the tests
  `tests/test_<app>_*.py`. Whoever fixes one app reads only those.
* Shared is only what knows about no app: the connection core and the settings rules
  (`jellyscope/services.py`) and the registry (`registry.py`), through which the web, Settings and the
  sync task ask about the apps.
* One app's package does not import another's.

This door imports nothing on purpose: `from jellyscope.arr import bazarr` loads only Bazarr, not the
other apps.
"""
