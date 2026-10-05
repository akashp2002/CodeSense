import requests
import json

# Login
res = requests.post('http://localhost:8001/login', data={'username': 'test@test.com', 'password': 'superlongpassword'*10})
token = res.json()['access_token']

# Save history
headers = {'Authorization': f'Bearer {token}'}
res_save = requests.post('http://localhost:8001/history', json={'history': '[{"role": "user", "content": "hello"}]'}, headers=headers)
print('Save:', res_save.status_code, res_save.text)

# Get history
res_get = requests.get('http://localhost:8001/history', headers=headers)
print('Get:', res_get.status_code, res_get.text)
