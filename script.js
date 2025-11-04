async function login(){
  const user=document.getElementById("user").value.trim();
  const pass=document.getElementById("pass").value.trim();
  if(!user||!pass){document.getElementById("status").innerText="⚠️ Enter username & password";return;}
  document.getElementById("status").innerText="Authenticating...";
  eel.login(user,pass)();
}
eel.expose(show_status);
function show_status(msg){document.getElementById("status").innerText=msg;}
eel.expose(redirect_otp);
function redirect_otp(username,status,timer){
  // Open OTP page inside same Eel-controlled browser window
  window.location.replace(`otp.html?user=${username}&status=${status}&t=${timer}`);
}

// -------- Random orbs (kept exactly) --------
const ring=document.querySelector(".neon-ring");
const ringRadius=160;
function spawnBubble(){
  const b=document.createElement("div");
  b.classList.add("bubble");
  const s=10+Math.random()*15;
  b.style.width=s+"px";b.style.height=s+"px";
  const a=Math.random()*2*Math.PI;
  const startX=Math.cos(a)*ringRadius;
  const startY=Math.sin(a)*ringRadius;
  b.style.left="50%";b.style.top="50%";
  b.style.transform=`translate(${startX}px,${startY}px) scale(1)`;
  ring.appendChild(b);
  const dist=80+Math.random()*120;
  const endX=Math.cos(a)*(ringRadius+dist);
  const endY=Math.sin(a)*(ringRadius+dist);
  requestAnimationFrame(()=>{
    b.style.transform=`translate(${endX}px,${endY}px) scale(${0.4+Math.random()*0.2})`;
    b.style.opacity="0";
  });
  setTimeout(()=>b.remove(),2600);
}
setInterval(()=>{for(let i=0;i<3+Math.floor(Math.random()*3);i++)spawnBubble();},150);



eel.expose(trigger_emergency_mode);
function trigger_emergency_mode(){
  const lock = document.getElementById("lockscreen");
  if(lock){
    lock.style.display = "flex";
  }
}
