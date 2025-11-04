const url=new URL(window.location.href);
const user=url.searchParams.get("user");
const status=(url.searchParams.get("status")||"customer").toLowerCase();
const menu=document.getElementById("menu");

menu.innerHTML=`<h1 style="color:#e7fdfd;">Welcome, ${user}</h1>
<p class="subtitle">${status==="staff"?"Staff Access Panel":"Customer Access Panel"}</p>`;

if(status==="staff"){
  menu.innerHTML+=`
    <button onclick="eel.toggle_maintenance(true)()">Enable Maintenance</button>
    <button onclick="eel.toggle_maintenance(false)()">Disable Maintenance</button>
    <button onclick="logout()">Logout</button>`;
}else{
  menu.innerHTML+=`
    <button onclick="checkBalance()">Check Balance</button>
    <button>Withdraw</button>
    <button>Deposit</button>
    <button onclick="logout()">Logout</button>`;
}

function logout(){ window.location.href="index.html"; }

// === New balance viewer ===
async function checkBalance(){
  const bal = await eel.get_balance(user)();   // wait for Python to return
  window.location.href = `balance.html?user=${user}&status=${status}&bal=${bal}`;
}

eel.expose(show_balance);
function show_balance(user, amount){
  window.location.href = `balance.html?user=${user}&status=customer`;
  setTimeout(()=>{
    document.getElementById("balance-amount").innerText = `RM ${amount}`;
  },300);
}

eel.expose(trigger_emergency_mode);
function trigger_emergency_mode(){
  const lock = document.getElementById("lockscreen");
  if(lock){
    lock.style.display = "flex";
  }
}







