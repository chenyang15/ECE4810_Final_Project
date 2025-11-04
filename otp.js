const url = new URL(window.location.href);
const username = url.searchParams.get("user");
const status = url.searchParams.get("status") || "customer";
let timeLeft = parseInt(url.searchParams.get("t")) || 60;

// ---------------- Countdown circle ----------------
const canvas = document.getElementById("circle");
const ctx = canvas.getContext("2d");
const r = 50;

function draw() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.beginPath();
  const p = timeLeft / 60;
  ctx.arc(60, 60, r, -Math.PI / 2, -Math.PI / 2 + p * 2 * Math.PI, false);
  ctx.strokeStyle = "#18ffff";
  ctx.lineWidth = 6;
  ctx.shadowBlur = 10;
  ctx.shadowColor = "#18ffff";
  ctx.stroke();
}

function tick() {
  draw();
  document.getElementById("countdown").innerText = `${timeLeft}s remaining`;
  if (timeLeft <= 0) {
    document.getElementById("status").innerText = "⏰ OTP expired";
    return;
  }
  timeLeft--;
  setTimeout(tick, 1000);
}
tick();

// ---------------- Submit OTP ----------------
function submitOtp() {
  const val = document.getElementById("otp").value.trim();
  if (!val || val.length !== 6) {
    document.getElementById("status").innerText = "⚠️ Enter 6 digits";
    return;
  }
  document.getElementById("status").innerText = "Verifying...";
  eel.verify_otp(username, val)();
}

// ---------------- From Python ----------------
eel.expose(otp_failed);
function otp_failed(msg) {
  document.getElementById("status").innerText = msg;
}

eel.expose(redirect_main);
function redirect_main(username, status) {
  window.location.replace(`mainmenu.html?user=${username}&status=${status}`);
}

// ---------------- Orb background ----------------
const ring = document.querySelector(".neon-ring");
const ringRadius = 160;

function spawnBubble() {
  const b = document.createElement("div");
  b.classList.add("bubble");
  const s = 10 + Math.random() * 15;
  b.style.width = s + "px";
  b.style.height = s + "px";

  const a = Math.random() * 2 * Math.PI;
  const startX = Math.cos(a) * ringRadius;
  const startY = Math.sin(a) * ringRadius;

  b.style.left = "50%";
  b.style.top = "50%";
  b.style.transform = `translate(${startX}px, ${startY}px) scale(1)`;
  ring.appendChild(b);

  const dist = 80 + Math.random() * 120;
  const endX = Math.cos(a) * (ringRadius + dist);
  const endY = Math.sin(a) * (ringRadius + dist);

  requestAnimationFrame(() => {
    b.style.transform = `translate(${endX}px, ${endY}px) scale(${0.4 + Math.random() * 0.2})`;
    b.style.opacity = "0";
  });

  setTimeout(() => b.remove(), 2600);
}

// spawn 3–6 bubbles every 150 ms
setInterval(() => {
  for (let i = 0; i < 3 + Math.floor(Math.random() * 3); i++) spawnBubble();
}, 150);

eel.expose(trigger_emergency_mode);
function trigger_emergency_mode(){
  const lock = document.getElementById("lockscreen");
  if(lock){
    lock.style.display = "flex";
  }
}


