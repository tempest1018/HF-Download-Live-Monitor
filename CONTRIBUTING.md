# Contributing

Install Python 3.10 or newer, then run:

```console
python -m pip install -e ".[dev]"
python -m ruff format --check .
python -m ruff check .
python -m pyright
python -m pytest
```

Changes should include a failing test first, preserve structured-output compatibility, avoid network access in routine tests, and never add credentials or private repository data. Keep modules focused and use public Hugging Face APIs outside the isolated compatibility module.
