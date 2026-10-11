// Adds a login form after load, so the original HTML and the final DOM differ.
document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("slot").innerHTML =
    '<form action="https://collect.test/login" method="post">' +
    '<input name="email"><input type="password" name="pw"><button>Sign in</button></form>';
});
