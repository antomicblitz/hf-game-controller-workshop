# Workshop agent rules

- This repository is for one exact-six USB game controller: four directional
  controls and action A/B.
- Read `docs/02-game-and-stl-viewer.md` before planning or changing the browser
  project.
- Student coding changes belong under `student-project/`. Do not change the
  case editor, controller geometry, or firmware while building a game.
- Games consume the semantic input adapter in
  `student-project/controller-input.js`; do not duplicate raw Gamepad API
  mappings.
- Keep the browser project plain HTML, CSS, and JavaScript. Do not add Node.js,
  a framework, a package manager, a database, or a remote server.
- Never place an API key, credential, personal data, or `.env` file in this
  repository. OpenCode owns provider authentication.
- Run the smallest relevant check while working and `make validate` before
  considering a change complete.
