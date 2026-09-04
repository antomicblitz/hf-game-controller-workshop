# HF Game Controller Workshop

Build a browser game, customize its six-button controller case, and wire the
working USB controller. No previous programming experience is required.

Use **macOS**, or **Ubuntu in WSL2** on Windows. PrusaSlicer is provided
separately for the workshop.

## 1. Set up OpenCode

Follow [Setup](docs/01-setup.md), then run:

```console
make setup
make connect
make doctor
```

## 2. Build your game and view STLs

Follow [Game and STL viewer](docs/02-game-and-stl-viewer.md).

```console
make agent
make web
```

## 3. Customize and print the case

Follow [Case editor and printing](docs/03-case-editor-and-print.md).

```console
make editor
```

Open the printed local URL. Download both STLs when finished. Press **Ctrl+C**
to stop the editor, then slice the files in PrusaSlicer and save the G-code to
an SD card.

## 4. Wire the controller

Follow [Wiring and firmware](docs/04-wiring-and-firmware.md).

If something does not work, run `make doctor` and show the result to Antonio.
