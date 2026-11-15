// Nelyqor — préparation de la photo de profil.
// L'image est recadrée en carré, réduite à 256 px et ré-encodée en JPEG :
//  - poids minime (≈ 20-40 Ko), synchronisable ;
//  - les métadonnées (EXIF, position GPS, modèle d'appareil) sont supprimées ;
//  - un fichier piégé n'est jamais conservé tel quel : seul le rendu du canvas est gardé.

const MAX_INPUT_BYTES = 15 * 1024 * 1024;
const SIZE = 256;
const ALLOWED = ['image/jpeg', 'image/png', 'image/webp', 'image/heic', 'image/heif', 'image/gif'];

function loadImage(file) {
  if (typeof createImageBitmap === 'function') {
    return createImageBitmap(file, { imageOrientation: 'from-image' }).catch(() => loadWithElement(file));
  }
  return loadWithElement(file);
}

function loadWithElement(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => { URL.revokeObjectURL(url); resolve(img); };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('decode')); };
    img.src = url;
  });
}

/** Fichier choisi → data URL JPEG 256×256. Lève une Error avec un message lisible. */
export async function fileToAvatar(file) {
  if (!file) throw new Error('Aucune image choisie.');
  if (file.type && !ALLOWED.includes(file.type)) throw new Error('Choisis une image (JPEG, PNG, WebP ou HEIC).');
  if (file.size > MAX_INPUT_BYTES) throw new Error('Cette image est trop lourde (15 Mo maximum).');
  let img;
  try { img = await loadImage(file); } catch { throw new Error('Impossible de lire cette image.'); }
  const w = img.width || img.naturalWidth;
  const hgt = img.height || img.naturalHeight;
  if (!w || !hgt) throw new Error('Impossible de lire cette image.');
  const side = Math.min(w, hgt);
  const canvas = document.createElement('canvas');
  canvas.width = SIZE;
  canvas.height = SIZE;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#0F1720';
  ctx.fillRect(0, 0, SIZE, SIZE);
  ctx.imageSmoothingQuality = 'high';
  ctx.drawImage(img, (w - side) / 2, (hgt - side) / 2, side, side, 0, 0, SIZE, SIZE);
  if (img.close) img.close();
  return canvas.toDataURL('image/jpeg', 0.85);
}

/** Ouvre le sélecteur de photo (galerie ou appareil photo sur mobile). */
export function pickImage() {
  return new Promise((resolve) => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/*';
    input.style.display = 'none';
    input.addEventListener('change', () => { resolve(input.files && input.files[0]); input.remove(); }, { once: true });
    document.body.append(input);
    input.click();
  });
}
