import json

FILE_PATH = 'data.json'
def load_data(file_path):
    with open(f"{file_path}", "r") as file:
        data = json.load(file)
        return data
    
def save_data(data):
    with open(FILE_PATH, "w") as f:
        json.dump(data, f)