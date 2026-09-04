# 1. Setup

The case editor runs locally on your computer. Internet access is used only for
OpenCode Zen and the browser's pinned 3D-viewer libraries.

## macOS

1. Open **Terminal**.
2. If `git`, `make`, or Python are missing, install Apple's command-line tools
   and Homebrew, then run:

   ```console
   xcode-select --install
   brew install python@3.12 cairo
   ```

3. Install OpenCode with its official installer:

   ```console
   curl -fsSL https://opencode.ai/install | bash
   ```

## Windows with WSL2

1. Open **PowerShell as Administrator**, run this once, then restart Windows:

   ```powershell
   wsl --install -d Ubuntu
   ```

2. Open **Ubuntu**, then install the prerequisites:

   ```console
   sudo apt update
   sudo apt install -y git make python3 python3-venv python3-pip bubblewrap libseccomp2 libcairo2
   curl -fsSL https://opencode.ai/install | bash
   ```

Run all later `make` commands in Ubuntu, not PowerShell. Your normal Windows
browser can open the printed `127.0.0.1` URLs.

## Get the workshop repository

```console
git clone https://github.com/antomicblitz/hf-game-controller-workshop.git
cd hf-game-controller-workshop
make setup
```

## Add the workshop Zen key

Antonio supplies the temporary key privately. Run:

```console
make connect
```

Choose **OpenCode Zen** if asked and paste the key only into OpenCode's hidden
credential prompt. Never paste it into a file, chat prompt, browser form,
design brief, screenshot, or Git commit.

The project already selects the tested workshop model. Check everything with:

```console
make doctor
```

Every line should begin with `OK`. After the workshop, remove the temporary
credential with `make disconnect` and choose OpenCode Zen.
