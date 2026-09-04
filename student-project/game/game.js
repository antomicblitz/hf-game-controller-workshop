import { readController } from "../controller-input.js";

const canvas = document.getElementById("game");
const context = canvas.getContext("2d");
const scoreLabel = document.getElementById("score");
const controllerLabel = document.getElementById("controller");
const player = { x: 100, y: 260, radius: 18 };
const star = { x: 700, y: 260, radius: 13 };
let score = 0;
let previous = { actionA: false, actionB: false };

function placeStar() {
  star.x = 40 + Math.random() * (canvas.width - 80);
  star.y = 40 + Math.random() * (canvas.height - 80);
}

function reset() {
  player.x = 100;
  player.y = canvas.height / 2;
  score = 0;
  scoreLabel.textContent = "Stars: 0";
  placeStar();
}

function update() {
  const input = readController();
  const speed = input.actionA ? 8 : 4;
  player.x += (Number(input.right) - Number(input.left)) * speed;
  player.y += (Number(input.down) - Number(input.up)) * speed;
  player.x = Math.max(player.radius, Math.min(canvas.width - player.radius, player.x));
  player.y = Math.max(player.radius, Math.min(canvas.height - player.radius, player.y));
  controllerLabel.textContent = input.connected ? `Controller: ${input.name}` : "Keyboard ready · connect USB controller anytime";

  if (input.actionB && !previous.actionB) reset();
  if (Math.hypot(player.x - star.x, player.y - star.y) < player.radius + star.radius) {
    score += 1;
    scoreLabel.textContent = `Stars: ${score}`;
    placeStar();
  }
  previous = input;
}

function draw() {
  context.clearRect(0, 0, canvas.width, canvas.height);
  context.fillStyle = "#ffd166";
  context.beginPath();
  context.arc(star.x, star.y, star.radius, 0, Math.PI * 2);
  context.fill();
  context.fillStyle = previous.actionA ? "#ff7b72" : "#6cc7ff";
  context.beginPath();
  context.arc(player.x, player.y, player.radius, 0, Math.PI * 2);
  context.fill();
}

function frame() {
  update();
  draw();
  requestAnimationFrame(frame);
}

reset();
frame();
