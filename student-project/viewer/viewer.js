import * as THREE from "three";
import { OrbitControls } from "https://cdn.jsdelivr.net/npm/three@0.180.0/examples/jsm/controls/OrbitControls.js";
import { STLLoader } from "https://cdn.jsdelivr.net/npm/three@0.180.0/examples/jsm/loaders/STLLoader.js";

const canvas = document.getElementById("viewer");
const status = document.getElementById("status");
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x070a10);
const camera = new THREE.PerspectiveCamera(45, 3 / 2, 0.1, 2000);
const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true;
scene.add(new THREE.HemisphereLight(0xffffff, 0x24304a, 2.5));
const key = new THREE.DirectionalLight(0xffffff, 3);
key.position.set(100, -80, 160);
scene.add(key);
const loader = new STLLoader();
let mesh = null;

function resize() {
  const width = canvas.clientWidth;
  const height = Math.max(360, Math.round(width * 2 / 3));
  renderer.setSize(width, height, false);
  camera.aspect = width / height;
  camera.updateProjectionMatrix();
}

function showGeometry(geometry, label) {
  if (mesh) {
    scene.remove(mesh);
    mesh.geometry.dispose();
    mesh.material.dispose();
  }
  geometry.computeBoundingBox();
  geometry.center();
  mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({ color: 0x6cc7ff, roughness: 0.55, metalness: 0.05 }));
  mesh.rotation.x = -Math.PI / 2;
  scene.add(mesh);
  resetView();
  status.textContent = label;
}

function resetView() {
  if (!mesh) return;
  const box = new THREE.Box3().setFromObject(mesh);
  const size = box.getSize(new THREE.Vector3()).length();
  camera.position.set(size * 0.8, size * 0.8, size * 0.8);
  controls.target.set(0, 0, 0);
  controls.update();
}

async function loadUrl(url, label) {
  status.textContent = `Loading ${label}…`;
  try {
    showGeometry(await loader.loadAsync(url), label);
  } catch (error) {
    status.textContent = `Could not load ${label}.`;
    console.error(error);
  }
}

document.querySelectorAll("[data-stl]").forEach(button => {
  button.addEventListener("click", () => loadUrl(button.dataset.stl, button.textContent));
});
document.getElementById("file").addEventListener("change", event => {
  const file = event.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.addEventListener("load", () => showGeometry(loader.parse(reader.result), file.name));
  reader.readAsArrayBuffer(file);
});
document.getElementById("reset").addEventListener("click", resetView);
window.addEventListener("resize", resize);

function frame() {
  resize();
  controls.update();
  renderer.render(scene, camera);
  requestAnimationFrame(frame);
}

loadUrl("../assets/case-top.stl", "Top case");
frame();
