const { JSDOM } = require('jsdom');
const dom = new JSDOM(`
<!DOCTYPE html>
<html><body>
<button class="tab-btn active" onclick="switchTab(this, 'subscribe-tab')">Subscribe</button>
<button class="tab-btn" onclick="switchTab(this, 'dashboard-tab')">Live Market</button>
<section id="subscribe-tab" class="tab-content active">Sub</section>
<section id="dashboard-tab" class="tab-content">Dash</section>
<script>
window.switchTab = function(btn, targetId) {
    const tabBtns = document.querySelectorAll('.tab-btn');
    const tabContents = document.querySelectorAll('.tab-content');
    tabBtns.forEach(b => b.classList.remove('active'));
    tabContents.forEach(c => c.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById(targetId).classList.add('active');
}
</script>
</body></html>
`, { runScripts: "dangerously" });
const window = dom.window;
const document = window.document;
const dashBtn = document.querySelectorAll('.tab-btn')[1];
dashBtn.click();
console.log("Dash Active:", document.getElementById('dashboard-tab').classList.contains('active'));
console.log("Sub Active:", document.getElementById('subscribe-tab').classList.contains('active'));
