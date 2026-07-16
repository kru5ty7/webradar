#!/bin/bash
sudo pkill -f "main.py" 2>/dev/null
sudo pkill -f "tsx index.ts" 2>/dev/null
sudo pkill -f "avitran_bridge" 2>/dev/null
pkill -f "vite" 2>/dev/null
pkill -f "node ws/app.js" 2>/dev/null
sudo fuser -k 22006/tcp 2>/dev/null
sudo fuser -k 9001/tcp 2>/dev/null
fuser -k 5173/tcp 2>/dev/null
echo "done"
