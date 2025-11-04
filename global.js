// ===========================
// GLOBAL EMERGENCY HANDLER
// ===========================

// Called from Python when the ATM door is pried open
eel.expose(trigger_emergency_mode);
function trigger_emergency_mode(){
  console.warn("⚠️  EMERGENCY MODE TRIGGERED ⚠️");

  // Remember state for all pages (persists across reloads)
  sessionStorage.setItem("lockdown", "true");

  // Show overlay immediately on this page
  const lock = document.getElementById("lockscreen");
  if (lock) {
    lock.style.display = "flex";
  } else {
    // If no overlay is present (rare), redirect to dedicated lock page
    window.location.replace("lock.html");
  }
}

// When any page loads, check if lockdown is already active
window.addEventListener("DOMContentLoaded", () => {
  if (sessionStorage.getItem("lockdown") === "true") {
    const lock = document.getElementById("lockscreen");
    if (lock) lock.style.display = "flex";
  }
});

// Optional manual reset for debugging (run in console: clearLock())
function clearLock(){
  sessionStorage.removeItem("lockdown");
  const lock = document.getElementById("lockscreen");
  if (lock) lock.style.display = "none";
  console.log("Lockdown cleared (manual reset).");
}



