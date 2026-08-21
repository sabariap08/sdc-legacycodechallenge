import subprocess
import sys
import os

def main():
    backend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend")
    print("=" * 60)
    print("  Legacy Code Rescue - Bug Bounty Challenge Portal")
    print("=" * 60)
    print()
    print("Starting server...")
    print("Admin Login:  http://localhost:8000/admin/login")
    print("Team Login:   http://localhost:8000/participant/login")
    print("Dashboard:    http://localhost:8000/")
    print()
    print("Default admin credentials:")
    print("  Username: admin")
    print("  Password: admin@123")
    print()

    subprocess.run(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"],
        cwd=backend_dir
    )

if __name__ == "__main__":
    main()
