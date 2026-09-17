const fs = require('fs');
const { JSDOM } = require('jsdom');
const html = fs.readFileSync('index.html', 'utf8');
const js = fs.readFileSync('main_app.js', 'utf8');
const dom = new JSDOM(html, { runScripts: "dangerously" });
const window = dom.window;
const document = window.document;

// Execute the JS
const scriptEl = document.createElement("script");
scriptEl.textContent = js;
document.body.appendChild(scriptEl);

// Find the dashboard button
const btns = document.querySelectorAll('.tab-btn');
console.log("Found buttons:", btns.length);
const dashBtn = Array.from(btns).find(b => b.getAttribute('onclick').includes('dashboard-tab'));

console.log("DashBtn text:", dashBtn.textContent);
dashBtn.click();

console.log("Dashboard Active Class:", document.getElementById('dashboard-tab').classList.contains('active'));
console.log("Subscribe Active Class:", document.getElementById('subscribe-tab').classList.contains('active'));
