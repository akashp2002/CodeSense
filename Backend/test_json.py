import json

state = {
    "impact_results": {
        "target_symbol": "get_db_session",
        "dependent_count": 2,
        "dependents": [
            {'file_path': 'D:\\CodeSense\\Backend\\repos\\demo\\backend\\app\\main.py', 'symbol_name': None, 'node_type': ['File']}, 
            {'file_path': 'D:\\CodeSense\\Backend\\repos\\demo\\backend\\app\\core\\security.py', 'symbol_name': None, 'node_type': ['File']}
        ],
        "summary": "Changing 'get_db_session' may impact 2 reference(s)..."
    }
}

try:
    print(json.dumps(state["impact_results"], indent=2))
except Exception as e:
    print(e)
