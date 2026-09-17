from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
import time

options = Options()
options.add_argument('--headless')
options.add_argument('--no-sandbox')
options.add_argument('--disable-dev-shm-usage')

try:
    driver = webdriver.Chrome(options=options)
    driver.get("http://localhost:8080/")
    time.sleep(2)
    
    # Print classes before
    dash_tab = driver.find_element(By.ID, "dashboard-tab")
    print("Before click Dash classes:", dash_tab.get_attribute("class"))
    
    # Click Live Market
    buttons = driver.find_elements(By.CLASS_NAME, "tab-btn")
    buttons[1].click()
    
    time.sleep(1)
    
    # Print classes after
    print("After click Dash classes:", dash_tab.get_attribute("class"))
    
    # Check for console errors
    logs = driver.get_log("browser")
    for log in logs:
        print("CONSOLE:", log)
    
    driver.quit()
except Exception as e:
    print("Error:", e)
