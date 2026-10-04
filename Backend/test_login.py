import requests

# Try login
res = requests.post("http://localhost:8001/login", data={"username": "test@test.com", "password": "superlongpassword"*10})
print("Login status:", res.status_code)
print("Login body:", res.text)
