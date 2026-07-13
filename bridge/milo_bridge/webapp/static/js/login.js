const form = document.getElementById("login-form");
const errorBox = document.getElementById("login-error");

form.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  errorBox.textContent = "";
  const username = document.getElementById("username").value;
  const password = document.getElementById("password").value;
  let data;
  try {
    const resp = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    data = await resp.json();
  } catch {
    errorBox.textContent = "network error — is the robot reachable?";
    return;
  }
  if (data.error) {
    errorBox.textContent = data.error;
    document.getElementById("password").value = "";
    return;
  }
  location.href = "/";
});
