const pressedKeys = new Set();
let connectedIndex = null;

const blockedKeys = new Set(["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "KeyZ", "KeyX"]);

window.addEventListener("keydown", event => {
  if (blockedKeys.has(event.code)) event.preventDefault();
  pressedKeys.add(event.code);
});
window.addEventListener("keyup", event => pressedKeys.delete(event.code));
window.addEventListener("blur", () => pressedKeys.clear());
window.addEventListener("gamepadconnected", event => { connectedIndex = event.gamepad.index; });
window.addEventListener("gamepaddisconnected", event => {
  if (connectedIndex === event.gamepad.index) connectedIndex = null;
});

function activeGamepad() {
  const gamepads = navigator.getGamepads ? Array.from(navigator.getGamepads()) : [];
  if (connectedIndex !== null && gamepads[connectedIndex]) return gamepads[connectedIndex];
  const compatible = gamepads.find(gamepad => gamepad && gamepad.axes.length >= 2 && gamepad.buttons.length >= 2);
  connectedIndex = compatible ? compatible.index : null;
  return compatible || null;
}

function down(button) {
  return Boolean(button && (button.pressed || button.value > 0.5));
}

export function readController(deadZone = 0.5) {
  const gamepad = activeGamepad();
  const x = gamepad?.axes[0] || 0;
  const y = gamepad?.axes[1] || 0;
  return {
    up: y < -deadZone || pressedKeys.has("ArrowUp"),
    down: y > deadZone || pressedKeys.has("ArrowDown"),
    left: x < -deadZone || pressedKeys.has("ArrowLeft"),
    right: x > deadZone || pressedKeys.has("ArrowRight"),
    actionA: down(gamepad?.buttons[0]) || pressedKeys.has("KeyZ"),
    actionB: down(gamepad?.buttons[1]) || pressedKeys.has("KeyX"),
    connected: Boolean(gamepad),
    name: gamepad?.id || "Keyboard",
  };
}
