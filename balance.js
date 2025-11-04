const url = new URL(window.location.href);
const username = url.searchParams.get("user");
const status = url.searchParams.get("status");
const bal = url.searchParams.get("bal");

document.getElementById("balance-amount").innerText =
  bal === "Error"
    ? "Error retrieving balance"
    : `RM ${parseFloat(bal).toFixed(2)}`;

function goBack(){
  window.location.replace(`mainmenu.html?user=${username}&status=${status}`);
}

eel.expose(trigger_emergency_mode);
function trigger_emergency_mode(){
  const lock = document.getElementById("lockscreen");
  if(lock){
    lock.style.display = "flex";
  }
}



