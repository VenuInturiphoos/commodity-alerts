// Supabase Configuration (User needs to replace these with their own)
const SUPABASE_URL = 'https://cohupetijvykzmeliubg.supabase.co';
const SUPABASE_ANON_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNvaHVwZXRpanZ5a3ptZWxpdWJnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODcxMjg4ODAsImV4cCI6MjEwMjcwNDg4MH0.zA5IwTKp0f-IRQ5dB3a9vXJSD1X2EVzxIDEyzXC27Cw';

// Initialize Supabase Client
// Note: In a real environment, you'd replace the above constants with your actual keys.
let supabase;
try {
    supabase = window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY);
} catch (e) {
    console.warn("Supabase not fully configured yet.");
}

document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('subscribe-form');
    const emailInput = document.getElementById('email-input');
    const statusMsg = document.getElementById('status-message');
    const submitBtn = document.getElementById('submit-btn');

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        
        const email = emailInput.value.trim();
        if (!email) return;

        // Visual feedback
        const originalBtnText = submitBtn.innerHTML;
        submitBtn.innerHTML = '<span>Submitting...</span>';
        submitBtn.disabled = true;
        
        try {
            // Check if Supabase is configured
            if (SUPABASE_URL === 'YOUR_SUPABASE_URL') {
                throw new Error("Database not connected yet. Please configure Supabase URL.");
            }

            // Insert into Supabase
            const { data, error } = await supabase
                .from('subscribers')
                .insert([{ email: email }]);

            if (error) throw error;

            // Success state
            statusMsg.textContent = "Success! You are now subscribed to alerts.";
            statusMsg.className = "success";
            emailInput.value = "";
            
        } catch (error) {
            // Error state
            console.error(error);
            statusMsg.textContent = error.message || "Something went wrong. Please try again.";
            statusMsg.className = "error";
        } finally {
            // Reset button
            submitBtn.innerHTML = originalBtnText;
            submitBtn.disabled = false;
        }
    });
});
