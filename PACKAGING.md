# Dong goi ung dung Windows

## Tao ban build

```powershell
.\env\Scripts\python.exe -m pip install -r requirements-build.txt
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build_windows.ps1
```

Ban build nam tai:

```text
dist\GiaiTichHUST\GiaiTichHUST.exe
```

Day la ban `onedir`. Khi chuyen sang may khac, phai chuyen ca thu muc
`dist\GiaiTichHUST`, khong chi rieng file `.exe`.

## Kiem tra ban build

```powershell
.\dist\GiaiTichHUST\GiaiTichHUST.exe --self-test
```

Ma thoat `0` co nghia la migrations, Waitress, templates, static va pywebview
da nap thanh cong.

## Du lieu nguoi dung

Ban dong goi luu database, secret key, log va de dang xu ly tai:

```text
%LOCALAPPDATA%\GiaiTichHUST
```

Lan chay dau tien, he thong tu tao tai khoan quan tri:

```text
Username: admin
Password: hustoneloveonefuture
```

Dang nhap tai khoan nay va doi mat khau ngay. Sinh vien co the tu dang ky va
su dung tai khoan. Dang ky giang vien phai duoc admin phe duyet truoc khi dang nhap.

Co the tao file `.env` trong thu muc du lieu tren de ghi de cau hinh. Vi du:

```env
APP_TIMEZONE=Asia/Bangkok
PDF_QUALITY_GATE=1
LATEX_ENGINE=C:\Program Files\MiKTeX\miktex\bin\x64\pdflatex.exe
```

## Phu thuoc ngoai

- Windows 10/11 can Microsoft Edge WebView2 Runtime de hien thi cua so native.
- Xuat PDF can MiKTeX hoac mot ban `pdflatex` khac.
- Bootstrap, MathJax, Font Awesome, AOS va CodeMirror da duoc dong goi trong
  `static/vendor`; giao dien va cong thuc Toan hoat dong ma khong can Internet.
- Internet chi can khi nguoi dung chu dong mo trang tai MiKTeX.
