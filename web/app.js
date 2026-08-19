// Supabase Configuration
const SUPABASE_URL = 'https://cohupetijvykzmeliubg.supabase.co';
const SUPABASE_ANON_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNvaHVwZXRpanZ5a3ptZWxpdWJnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODcxMjg4ODAsImV4cCI6MjEwMjcwNDg4MH0.zA5IwTKp0f-IRQ5dB3a9vXJSD1X2EVzxIDEyzXC27Cw';

let supabase;
try {
    supabase = window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY);
} catch (e) {
    console.warn("Supabase not fully configured yet.");
}

let marketData = [];

document.addEventListener('DOMContentLoaded', () => {
    setupTabs();
    setupSubscribeForm();
    setupFilters();
    fetchMarketData();
    
    // Auto refresh data every 5 minutes while dashboard is open
    setInterval(fetchMarketData, 300000); 
});

function setupTabs() {
    const tabBtns = document.querySelectorAll('.tab-btn');
    const tabContents = document.querySelectorAll('.tab-content');

    tabBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            // Remove active class from all
            tabBtns.forEach(b => b.classList.remove('active'));
            tabContents.forEach(c => c.classList.remove('active'));
            
            // Add active class to clicked
            btn.classList.add('active');
            const targetId = btn.getAttribute('data-target');
            document.getElementById(targetId).classList.add('active');
            
            // If switching to dashboard, refresh data
            if (targetId === 'dashboard-tab' || targetId === 'alerts-tab') {
                fetchMarketData();
            }
        });
    });
}

function setupFilters() {
    const filterBtns = document.querySelectorAll('.filter-btn');
    filterBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            filterBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            renderMarketTable(btn.getAttribute('data-filter'));
        });
    });
}

function setupSubscribeForm() {
    const form = document.getElementById('subscribe-form');
    const emailInput = document.getElementById('email-input');
    const statusMsg = document.getElementById('status-message');
    const submitBtn = document.getElementById('submit-btn');

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const email = emailInput.value.trim();
        if (!email) return;

        const originalBtnText = submitBtn.innerHTML;
        submitBtn.innerHTML = '<span>Submitting...</span>';
        submitBtn.disabled = true;
        
        try {
            const { data, error } = await supabase
                .from('subscribers')
                .insert([{ email: email }]);

            if (error) throw error;

            statusMsg.textContent = "Success! You are now subscribed to alerts.";
            statusMsg.className = "success";
            emailInput.value = "";
        } catch (error) {
            statusMsg.textContent = error.message || "Something went wrong. Please try again.";
            statusMsg.className = "error";
        } finally {
            submitBtn.innerHTML = originalBtnText;
            submitBtn.disabled = false;
        }
    });
}

async function fetchMarketData() {
    if (!supabase) return;
    
    try {
        const { data, error } = await supabase
            .from('market_data')
            .select('*')
            .order('name', { ascending: true });

        if (error) throw error;
        
        marketData = data;
        
        // Render both tables
        const activeFilter = document.querySelector('.filter-btn.active').getAttribute('data-filter');
        renderMarketTable(activeFilter);
        renderAlertTable();
        
    } catch (error) {
        console.error("Error fetching market data:", error);
        document.getElementById('market-data-body').innerHTML = `<tr><td colspan="6" class="error">Failed to load live data. Check console.</td></tr>`;
    }
}

function renderMarketTable(filter) {
    const tbody = document.getElementById('market-data-body');
    tbody.innerHTML = '';
    
    let filteredData = marketData;
    if (filter !== 'all') {
        filteredData = marketData.filter(item => item.asset_type === filter);
    }
    
    if (filteredData.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" class="loading">No data available. Waiting for cloud sync...</td></tr>`;
        return;
    }

    filteredData.forEach(item => {
        const typeClass = item.asset_type === 'Stock' ? 'type-stock' : 'type-commodity';
        const date = new Date(item.last_updated).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
        
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td><strong>${item.name}</strong> <small style="color:var(--text-secondary)">(${item.symbol})</small></td>
            <td><span class="type-badge ${typeClass}">${item.asset_type}</span></td>
            <td style="font-weight:600">₹${parseFloat(item.current_price).toFixed(2)}</td>
            <td style="color:#fb7185">₹${parseFloat(item.s1).toFixed(2)}</td>
            <td style="color:#4ade80">₹${parseFloat(item.r1).toFixed(2)}</td>
            <td style="color:var(--text-secondary); font-size: 0.875rem;">${date}</td>
        `;
        tbody.appendChild(tr);
    });
}

function renderAlertTable() {
    const tbody = document.getElementById('alert-data-body');
    tbody.innerHTML = '';
    
    // Filter only items that have an alert status
    const alertData = marketData.filter(item => item.alert_status && item.alert_status.trim() !== '');
    
    if (alertData.length === 0) {
        tbody.innerHTML = `<tr><td colspan="4" class="loading" style="padding: 4rem !important;">No assets currently in the alert zone. The market is quiet! 💤</td></tr>`;
        return;
    }

    alertData.forEach(item => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td><strong>${item.name}</strong> <small style="color:var(--text-secondary)">(${item.symbol})</small></td>
            <td style="font-weight:600; font-size: 1.1rem;">₹${parseFloat(item.current_price).toFixed(2)}</td>
            <td><span class="alert-tag">${item.alert_status.toUpperCase()}</span></td>
            <td>Action Required</td>
        `;
        tbody.appendChild(tr);
    });
}
