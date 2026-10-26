"""
TraceRule AI - Quick Start Script
==================================

This script starts:
1. The document analyzer backend API (port 8000)
2. Opens the frontend in your browser

Run this from the TraceRule-AI directory.
"""

import os
import sys
import webbrowser
import time
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent

def print_banner():
    print("=" * 60)
    print("  TraceRule AI - Real-Time Document Analyzer")
    print("=" * 60)
    print()

def install_dependencies():
    """Install required dependencies."""
    print("[1/3] Checking dependencies...")
    
    # Check if fastapi is installed
    try:
        import fastapi
        print("  ✓ FastAPI already installed")
    except ImportError:
        print("  Installing FastAPI and dependencies...")
        subprocess.run([
            sys.executable, "-m", "pip", "install", 
            "fastapi>=0.104.0", 
            "uvicorn>=0.24.0",
            "pymupdf>=1.23.0",
            "python-multipart>=0.0.6"
        ], check=True)
        print("  ✓ Dependencies installed")

def start_backend():
    """Start the backend API server."""
    print("\n[2/3] Starting backend API server...")
    
    backend_path = PROJECT_ROOT / "backend" / "services" / "document_analyzer.py"
    
    if not backend_path.exists():
        print(f"  ✗ Backend file not found: {backend_path}")
        return False
    
    # Start the server in background
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.services.document_analyzer:app", "--host", "0.0.0.0", "--port", "8000"],
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}
    )
    
    # Wait for server to start
    print("  Waiting for server to start...")
    time.sleep(3)
    
    print("  ✓ Backend running at http://localhost:8000")
    return True

def open_frontend():
    """Open the frontend in browser."""
    print("\n[3/3] Opening frontend...")
    
    frontend_path = PROJECT_ROOT / "frontend" / "index.html"
    
    if not frontend_path.exists():
        print(f"  ✗ Frontend file not found: {frontend_path}")
        return False
    
    # Try to serve with Python's http.server
    server_process = subprocess.Popen(
        [sys.executable, "-m", "http.server", "8080"],
        cwd=str(frontend_path.parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    
    time.sleep(1)
    
    # Open browser
    webbrowser.open("http://localhost:8080")
    
    print("  ✓ Frontend opened at http://localhost:8080")
    return True

def main():
    print_banner()
    
    print("This will start:")
    print("  - Backend API: http://localhost:8000")
    print("  - Frontend:    http://localhost:8080")
    print()
    
    try:
        install_dependencies()
        
        if start_backend():
            time.sleep(1)
            open_frontend()
        
        print("\n" + "=" * 60)
        print("  All systems running!")
        print("=" * 60)
        print("\nOpen http://localhost:8080 in your browser to use the app")
        print("Press Ctrl+C to stop the servers")
        
        # Keep running
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\n\nShutting down...")
        sys.exit(0)
    except Exception as e:
        print(f"\nError: {e}")
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main())