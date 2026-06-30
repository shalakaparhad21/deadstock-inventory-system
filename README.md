# Deadstock Inventory Management System

A full-stack web application to manage, track, and sustainably allocate deadstock inventory across multiple warehouse branches.

Built using Flask and MySQL, this project simulates a real-world enterprise inventory management system with role-based access control, reporting, and sustainability tracking.

---

## 🚀 Project Overview

The Deadstock Inventory Management System helps organizations reduce waste and optimize unused inventory (deadstock) by tracking items, managing warehouses, allocating stock responsibly, and generating analytical reports.

This project demonstrates full-stack development skills including backend logic, database design, authentication, and reporting.

---

## ✨ Key Features

### 🔐 Role-Based Access Control
- Admin
- Branch
- Warehouse
- Stock Allocation

Each role has restricted permissions similar to real enterprise systems.

---

### 🔑 Authentication & Security
- OTP-based forgot password / reset flow for all roles, including Admin
- 6-digit OTP with 10-minute expiry, retry limits, and secure token-based reset
- OTP delivery via Gmail SMTP (email)
- Session-based role access control

---

### 📦 Inventory Management
- Add and manage deadstock items
- SKU-based tracking
- Warehouse and branch management
- Warehouse capacity visualization
- QR code generation for items

---

### 🔄 Deadstock Allocation
- Allocate deadstock for:
  - Recycle
  - Donate
  - Resell
  - Upcycle
  - Disposal
  - Rebrand
- Bulk allocation support
- Allocation history tracking

---

### 📊 Reports & Sustainability
- PDF report generation using ReportLab
- Branch-wise and allocation reports
- Sustainability rating auto-calculated via MySQL triggers (`trg_update_branch_rating`)
- Multi-channel alerting: WhatsApp via Twilio API, email via SendGrid SMTP
- Automated email delivery of reports
---

## 🛠️ Tech Stack

| Layer | Technology |
|------|------------|
| Backend | Python, Flask |
| Database | MySQL |
| Frontend | HTML, CSS, JavaScript |
| Reports | ReportLab (PDF) |
| QR Codes | qrcode, Pillow |
| Authentication | Session-based RBAC |
| Notifications | Twilio (WhatsApp), SendGrid (Email) |

---
## 👥 User Roles

| Role | Description |
|-----|------------|
| Admin | Full system access, reports, analytics |
| Branch | View deadstock and materials |
| Warehouse | Manage warehouse data |
| Stock Allocation | Allocate deadstock and generate reports |

---

## 🎯 Learning Outcomes

- Built a real-world inventory management system
- Implemented role-based authentication and authorization
- Designed and integrated a relational database
- Generated PDF reports programmatically
- Implemented QR code generation
- Gained hands-on experience with Flask architecture

---

## 📜 License

This project is licensed under the MIT License.


