# 2. Build a game and view STL files

The coding harness starts in **Plan** mode so you can describe an idea before
anything changes.

```console
make agent
```

Try a prompt such as:

> Plan a one-screen game about collecting lost satellites. Make it playable in
> under one minute and use both action buttons.

Review the plan, switch to the **Build** agent, and ask it to implement the
plan. The agent changes only `student-project/` and keeps the project as plain
HTML, CSS, and JavaScript.

Start the local site in another terminal:

```console
make web
```

Open <http://127.0.0.1:8000>. Press **Ctrl+C** in that terminal to stop it.

## Controller contract

Games use `student-project/controller-input.js`, which returns these six names:

| Game input | USB gamepad | Keyboard fallback |
|---|---|---|
| up / down | Y axis, index 1 | Up / Down Arrow |
| left / right | X axis, index 0 | Left / Right Arrow |
| action A | button index 0 | Z |
| action B | button index 1 | X |

Do not assume the controller is gamepad number zero. The supplied adapter finds
a compatible connected controller, handles reconnects, and applies a digital
dead zone. Build game rules against its semantic values instead of reading
`navigator.getGamepads()` again.

**Super Maga Bros** is the in-class reference for a small controller game. Its
public link will be added after Antonio supplies it; this repository does not
copy or depend on its code.

## STL viewer

Choose **STL viewer** on the project home page. It can load the bundled top and
bottom case or an STL selected from your computer. Selected files stay in the
browser and are not uploaded.

Run `make web-check` after changing the game or viewer.
