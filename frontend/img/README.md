# Arac Gorselleri

Bu dizin, arac kartlarinda kullanilan istege bagli gorselleri barindirir.
Bos birakilirsa arayuz her arac icin yerlesik SVG cizimini gosterir.

## Gorsel ekleme

1. Dosyayi bu dizine koyun (`frontend/img/`).
2. `backend/data/vehicles.json` icinde ilgili modelin `image` alanina yolu yazin:

```json
{
  "id": "actros",
  "name": "Actros",
  "image": "img/mercedes-benz-actros.jpg",
  "image_credit": "Foto: Ad Soyad — CC BY-SA 4.0"
}
```

3. Servisi yeniden baslatin: `docker compose up --build`

Dosya bulunamazsa veya yuklenemezse arayuz sessizce SVG cizimine doner;
bozuk gorsel ikonu cikmaz.

## Oneriler

| Konu | Deger |
|---|---|
| En-boy orani | 320 × 132 (yaklasik 2.4:1) — kart alaniyla ayni |
| Cozunurluk | 640 × 264 piksel yeterli (2x ekranlar icin) |
| Bicim | `.webp` tercih edilir, `.jpg` de olur |
| Dosya boyutu | Kart basina 60 KB alti hedefleyin |
| Arka plan | Yan profil, sade arka plan en iyi sonucu verir |

## Telif

**Buraya yalnizca kullanim hakkina sahip oldugunuz gorselleri koyun.**

Uretici sitelerindeki (Mercedes-Benz, Volvo, MAN vb.) basin ve tanitim
fotograflari telif hakkiyla korunur; herkese acik bir depoda yayinlanmalari
ihlal olusturur. Guvenli kaynaklar:

- Kendi cektiginiz fotograflar
- Wikimedia Commons uzerindeki Creative Commons lisansli gorseller
  (lisans ne diyorsa o sekilde atif verin — `image_credit` alanini kullanin)
- Ureticiden yazili izin aldiginiz basin kiti gorselleri

CC lisansli bir gorsel kullaniyorsaniz `image_credit` alanini doldurun;
arayuz bu bilgiyi secili aracin altinda gosterir.
