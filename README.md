StreamHib
StreamHib adalah aplikasi berbasis Flask untuk mengelola streaming video. Aplikasi ini memungkinkan pengguna untuk mengunduh video dari Google Drive, mengelola file video, serta menjadwalkan dan menjalankan live streaming ke platform seperti YouTube dan Facebook. Aplikasi ini dilengkapi dengan antarmuka web yang modern, pemantauan penggunaan sistem (CPU, memory, disk), dan notifikasi Telegram.
Fitur

Unduh Video dari Google Drive: Unduh file video menggunakan ID atau URL Google Drive.
Manajemen Video: Ganti nama atau hapus video yang tersimpan di server.
Live Streaming: Mulai streaming langsung ke YouTube atau Facebook menggunakan stream key.
Jadwal Streaming: Jadwalkan streaming otomatis pada waktu tertentu.
Manajemen Sesi: Lihat sesi aktif, nonaktif, dan jadwal streaming.
Pemantauan Sistem: Tampilkan penggunaan CPU, memory, dan disk di header.
Notifikasi Telegram: Kirim notifikasi ke Telegram untuk setiap aksi penting.
Desain Modern: Antarmuka yang elegan, responsif, dan profesional menggunakan Tailwind CSS.

Prasyarat

Sistem operasi berbasis Linux (Ubuntu/Debian direkomendasikan).
Python 3.10 atau lebih baru.
FFmpeg untuk streaming video.
Akses root atau pengguna dengan hak sudo.
Koneksi internet untuk mengunduh dependensi dan streaming.

Instalasi
1. Clone Repository
Clone repository ini ke server Anda:
git clone https://github.com/masdonik/streamhib.git
cd streamhib

2. Instal Dependensi Sistem
Instal package yang diperlukan:
sudo apt update
sudo apt install python3 python3-pip python3-venv ffmpeg

3. Buat dan Aktifkan Virtual Environment
Buat virtual environment untuk mengisolasi dependensi:
python3 -m venv venv
source venv/bin/activate

4. Instal Dependensi Python
Instal semua dependensi yang diperlukan:
pip install flask gunicorn requests schedule psutil

5. Konfigurasi Aplikasi

Pastikan Anda memiliki Google Drive API Key untuk mengunduh file dari Google Drive.
Jika ingin menggunakan notifikasi Telegram, siapkan Telegram Bot Token dan Chat ID.
Konfigurasi ini dapat diatur melalui menu "Settings" di aplikasi setelah aplikasi berjalan.

6. Uji Coba Aplikasi
Jalankan aplikasi untuk memastikan semuanya berfungsi:
gunicorn --bind 0.0.0.0:4545 --timeout 300 app:app


Buka browser dan akses http://<IP_SERVER>:4545.
Login dengan username: admin dan password: pass12345.

7. Atur Aplikasi untuk Berjalan Otomatis
Agar aplikasi berjalan otomatis saat server reboot, gunakan systemd:
a. Buat File Service
Buat file service untuk systemd:
sudo nano /etc/systemd/system/streamhib.service

Tambahkan konfigurasi berikut:
[Unit]
Description=StreamHib Flask Application
After=network.target

[Service]
User=root
Group=root
WorkingDirectory=/root/streamhib
Environment="PATH=/root/streamhib/venv/bin"
ExecStart=/root/streamhib/venv/bin/gunicorn --bind 0.0.0.0:4545 --timeout 300 app:app
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target

Simpan dan keluar (Ctrl+O, lalu Ctrl+X).
b. Reload dan Aktifkan Service
Reload konfigurasi systemd, aktifkan, dan jalankan service:
sudo systemctl daemon-reload
sudo systemctl enable streamhib.service
sudo systemctl start streamhib.service

c. Periksa Status
Pastikan service berjalan:
sudo systemctl status streamhib.service

d. Uji Reboot
Reboot server untuk memastikan aplikasi berjalan otomatis:
sudo reboot

Setelah server menyala, periksa status lagi:
sudo systemctl status streamhib.service

8. (Opsional) Gunakan Pengguna Non-Root
Untuk keamanan, jalankan aplikasi sebagai pengguna non-root:

Buat pengguna baru:
sudo adduser --disabled-password --gecos "" streamhib


Pindahkan direktori aplikasi:
sudo mv /root/streamhib /home/streamhib/
sudo chown -R streamhib:streamhib /home/streamhib


Perbarui file streamhib.service:
[Unit]
Description=StreamHib Flask Application
After=network.target

[Service]
User=streamhib
Group=streamhib
WorkingDirectory=/home/streamhib/streamhib
Environment="PATH=/home/streamhib/streamhib/venv/bin"
ExecStart=/home/streamhib/streamhib/venv/bin/gunicorn --bind 0.0.0.0:4545 --timeout 300 app:app
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target


Reload dan restart service:
sudo systemctl daemon-reload
sudo systemctl restart streamhib.service



9. (Opsional) Gunakan Nginx sebagai Reverse Proxy
Untuk mengakses aplikasi melalui port 80/443:

Instal Nginx:
sudo apt install nginx


Buat konfigurasi Nginx:
sudo nano /etc/nginx/sites-available/streamhib

Tambahkan:
server {
    listen 80;
    server_name your_domain_or_ip;

    location / {
        proxy_pass http://127.0.0.1:4545;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}


Aktifkan konfigurasi:
sudo ln -s /etc/nginx/sites-available/streamhib /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx



Penggunaan

Akses aplikasi di http://<IP_SERVER>:4545 (atau melalui domain jika menggunakan Nginx).
Login dengan username: admin dan password: pass12345.
Konfigurasi API Key dan Telegram di menu "Settings".
Gunakan fitur-fitur seperti unduh video, kelola video, dan streaming sesuai kebutuhan.

Catatan

Keamanan: Gunakan HTTPS dengan Let's Encrypt jika aplikasi diakses publik:
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d your_domain


Backup: Simpan kode dan konfigurasi di GitHub untuk cadangan.

Log: Cek log aplikasi di streamhib.log atau log service dengan:
journalctl -u streamhib.service -b



Kontribusi
Silakan fork repository ini, buat perubahan, dan ajukan pull request untuk berkontribusi.
Lisensi
Proyek ini dilisensikan di bawah MIT License.
