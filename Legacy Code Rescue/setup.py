import os
import sys
import subprocess

def ensure_directories():
    dirs = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "challenge_storage"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "team_workspaces"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "evaluator"),
    ]
    for d in dirs:
        os.makedirs(d, exist_ok=True)
        gitkeep = os.path.join(d, ".gitkeep")
        if not os.path.exists(gitkeep):
            with open(gitkeep, 'w') as f:
                pass

def install_requirements():
    req_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend", "requirements.txt")
    print("Installing Python dependencies...")
    subprocess.run([sys.executable, "-m", "pip", "install", "-r", req_file], check=False)

def check_mongodb():
    try:
        from pymongo import MongoClient
        client = MongoClient("mongodb://localhost:27017", serverSelectionTimeoutMS=3000)
        client.server_info()
        print("MongoDB is running ✓")
        return True
    except Exception as e:
        print(f"MongoDB is NOT running: {e}")
        print("Please start MongoDB before running the server.")
        print("  Windows: net start MongoDB")
        print("  Or install from: https://www.mongodb.com/try/download/community")
        return False

def main():
    print("=" * 60)
    print("  Legacy Code Rescue - Setup")
    print("=" * 60)
    print()

    ensure_directories()
    print("Project directories created ✓")

    install_requirements()
    print("Dependencies installed ✓")

    if check_mongodb():
        print()
        print("Setup complete! Run: python run.py")
    else:
        print()
        print("Fix MongoDB and then run: python run.py")

if __name__ == "__main__":
    main()
