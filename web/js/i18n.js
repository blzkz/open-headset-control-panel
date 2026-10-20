// English source strings → Spanish; language follows the browser (override with ?lang=en|es).

const ES = {
  "Connect headset": "Conectar auriculares",
  "Disconnect": "Desconectar",
  "Control your Logitech headset from the browser": "Controla tus auriculares Logitech desde el navegador",
  "Equalizer, sidetone, lighting, auto power-off and battery over WebHID — nothing to install.":
    "Ecualizador, sidetone, iluminación, apagado automático y batería mediante WebHID, sin instalar nada.",
  "Supported headsets": "Auriculares compatibles",
  "experimental": "experimental",
  "Your browser does not support WebHID. Use Chrome, Edge, Brave or Opera on desktop.":
    "Tu navegador no es compatible con WebHID. Usa Chrome, Edge, Brave u Opera en un ordenador.",
  "On Linux the browser needs permission to open the headset:": "En Linux el navegador necesita permiso para abrir el auricular:",
  "download the udev rule": "descarga la regla udev",
  "Want voice enhancement, 7.1 surround, a tray icon and profiles applied automatically? Get the desktop app.":
    "¿Quieres mejora de voz, 7.1 virtual, icono en la bandeja y perfiles automáticos? Instala la app de escritorio.",
  "Desktop app": "App de escritorio",
  "Profile": "Perfil",
  "Profiles": "Perfiles",
  "New profile…": "Nuevo perfil…",
  "Duplicate profile…": "Duplicar perfil…",
  "Rename profile…": "Renombrar perfil…",
  "Delete profile": "Eliminar perfil",
  "Profile name": "Nombre del perfil",
  "Delete profile “{name}”?": "¿Eliminar el perfil «{name}»?",
  "Equalizer": "Ecualizador",
  "Microphone": "Micrófono",
  "Lighting": "Iluminación",
  "Device": "Dispositivo",
  "Presets": "Preajustes",
  "My presets": "Mis preajustes",
  "Custom": "Personalizado",
  "Reset": "Restablecer",
  "Save as preset…": "Guardar como preajuste…",
  "Delete preset": "Eliminar preajuste",
  "Preset name": "Nombre del preajuste",
  "Onboard 10-band equalizer · stored in the headset": "Ecualizador integrado de 10 bandas · se guarda en los auriculares",
  "This model has no onboard equalizer": "Este modelo no tiene ecualizador integrado",
  "Drag the points, use the mouse wheel or arrow keys. Double-click resets a band.":
    "Arrastra los puntos, usa la rueda del ratón o las flechas. Doble clic restablece una banda.",
  "Flat": "Plano",
  "Bass Boost": "Refuerzo de graves",
  "FPS (footsteps)": "FPS (pasos)",
  "MOBA": "MOBA",
  "Movie": "Cine",
  "Music": "Música",
  "Voice / Podcast": "Voz / Podcast",
  "Treble Boost": "Refuerzo de agudos",
  "Pro Esports": "Pro Esports",
  "Sidetone": "Tono lateral (sidetone)",
  "Hear your own voice in the headset": "Escucha tu propia voz en los auriculares",
  "Level": "Nivel",
  "This model has no sidetone control": "Este modelo no tiene control de sidetone",
  "Effect": "Efecto",
  "Off": "Apagado",
  "Static": "Fijo",
  "Breathing": "Respiración",
  "Colour": "Color",
  "Brightness": "Brillo",
  "Applies to every lighting zone of the headset": "Se aplica a todas las zonas de iluminación del auricular",
  "Device information": "Información del dispositivo",
  "Name": "Nombre",
  "Model": "Modelo",
  "Firmware": "Firmware",
  "HID++ protocol": "Protocolo HID++",
  "USB ID": "ID USB",
  "HID++ features": "Funciones HID++",
  "Battery": "Batería",
  "Battery voltage": "Tensión de batería",
  "Power": "Energía",
  "Auto power-off": "Apagado automático",
  "Turn the headset off after this long without audio": "Apaga el auricular tras este tiempo sin audio",
  "Never": "Nunca",
  "{n} min": "{n} min",
  "Charging": "Cargando",
  "Fully charged": "Carga completa",
  "Discharging": "En uso",
  "≈ {h} h remaining": "≈ {h} h restantes",
  "≈ {h} h to full": "≈ {h} h para carga completa",
  "Headset off": "Auriculares apagados",
  "Turn on your headset": "Enciende tus auriculares",
  "Reconnecting…": "Reconectando…",
  "Experimental support": "Soporte experimental",
  "This model has not been tested on real hardware yet.": "Este modelo aún no se ha probado con el hardware real.",
  "Select your headset in the browser dialog": "Selecciona tus auriculares en el diálogo del navegador",
  "Could not open the headset": "No se pudo abrir el auricular",
  "Connected": "Conectado",
  "Settings are applied to the headset instantly and remembered in this browser.":
    "Los ajustes se aplican al auricular al instante y se recuerdan en este navegador.",
  "Language": "Idioma",
};

const params = new URLSearchParams(location.search);
export const lang = (params.get("lang") || navigator.language || "en").toLowerCase().startsWith("es") ? "es" : "en";

export function tr(text, vars = {}) {
  const out = lang === "es" ? ES[text] ?? text : text;
  return out.replace(/\{(\w+)\}/g, (_, k) => vars[k] ?? `{${k}}`);
}

export function applyStatic(root = document) {
  document.documentElement.lang = lang;
  root.querySelectorAll("[data-i18n]").forEach((el) => {
    el.textContent = tr(el.dataset.i18n);
  });
  root.querySelectorAll("[data-i18n-title]").forEach((el) => {
    el.title = tr(el.dataset.i18nTitle);
  });
}
