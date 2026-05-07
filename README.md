# QC Inspection System - Carton Quality Control Dashboard

A Python desktop application for industrial carton and bag quality control inspection.
The system is designed for production QC teams to record carton condition, bag weight, metal detector test, label check, dirty check, plastic check, final pass/fail status, and inspection history.

## Main Features

- QC login portal
- Start new carton inspection workflow
- Auto-generated QC number
- Carton number lookup from MySQL
- Production details lookup from MySQL
- Digital weighing scale popup for bag weight entry
- Bag-by-bag inspection progress
- Pass / Fail / Pending status tracking
- Failed carton overview
- Inspection history with filters
- Dashboard summary for QC performance
- MySQL data logging

## Screenshots

### Login Portal
![Login Portal](assets/login_portal.png)

### Home Page
![Home Page](assets/home_page.png)

### Start New Inspection Form
![Start New Inspection](assets/start_new_inspection.png)

### Digital Weight Scale Popup
![Digital Weight Scale Popup](assets/digital_weight_scale_popup.png)

### Failed Cartons Overview
![Failed Cartons Overview](assets/failed_cartons_overview.png)

### Inspection History
![Inspection History](assets/inspection_history.png)

### Bag Details
![Bag Details](assets/bag_details.png)

## Tech Stack

- Python
- PySide6 / Qt
- MySQL
- Modbus scale integration using pymodbus
- Windows batch launcher included

## Installation

```bash
pip install -r requirements.txt
```

## Configuration

Do not hard-code real MySQL passwords in public GitHub repositories.
Use environment variables instead. A sample file is provided:

```bash
.env.example
```

Set your MySQL details before running the app:

```bash
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=root
DB_PASSWORD=your_mysql_password
DB_NAME=gama
```

## Run

Windows:

```bat
run_qc.bat
```

Linux / Jetson / Ubuntu:

```bash
./run_qc.sh
```

Or run directly:

```bash
python qc_form.py
```

## Notes

This repository is prepared for portfolio/demo use. Before using in production, update database credentials, table schema, COM port settings, and scale register settings based on your factory setup.

## Suggested Repository Name

```text
qc-inspection-system
```
