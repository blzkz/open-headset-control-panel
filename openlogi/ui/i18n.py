"""Tiny translation layer: English source strings, Spanish catalogue."""

from __future__ import annotations

import os

_ES: dict[str, str] = {
    # navigation / chrome
    "Equalizer": "Ecualizador",
    "Microphone": "Micrófono",
    "Surround": "Sonido envolvente",
    "Audio": "Audio",
    "Device": "Dispositivo",
    "Settings": "Ajustes",
    "Profile": "Perfil",
    "Back": "Volver",
    "Minimize": "Minimizar",
    "Maximize": "Maximizar",
    "Close": "Cerrar",
    # states
    "Connected": "Conectado",
    "Headset off": "Auriculares apagados",
    "Receiver not found": "Receptor no encontrado",
    "No permission": "Sin permisos",
    "Charging": "Cargando",
    "Fully charged": "Carga completa",
    "Discharging": "En uso",
    "USB cable": "Cable USB",
    "LIGHTSPEED receiver": "Receptor LIGHTSPEED",
    "Turn on your headset": "Enciende tus auriculares",
    "Plug in the LIGHTSPEED receiver": "Conecta el receptor LIGHTSPEED",
    "Grant access to the hidraw device (see README: udev rule)":
        "Concede acceso al dispositivo hidraw (ver README: regla udev)",
    "Click the headset to configure it": "Haz clic en los auriculares para configurarlos",
    "≈ {h} h remaining": "≈ {h} h restantes",
    "≈ {h} h to full": "≈ {h} h para carga completa",
    # equalizer
    "Presets": "Preajustes",
    "My presets": "Mis preajustes",
    "Custom": "Personalizado",
    "Flat": "Plano",
    "Bass Boost": "Refuerzo de graves",
    "FPS (footsteps)": "FPS (pasos)",
    "MOBA": "MOBA",
    "Movie": "Cine",
    "Music": "Música",
    "Voice / Podcast": "Voz / Podcast",
    "Treble Boost": "Refuerzo de agudos",
    "Pro Esports": "Pro Esports",
    "Save as preset…": "Guardar como preajuste…",
    "Delete preset": "Eliminar preajuste",
    "Reset": "Restablecer",
    "Preset name": "Nombre del preajuste",
    "Onboard 10-band equalizer · stored in the headset": "Ecualizador integrado de 10 bandas · se guarda en los auriculares",
    "Drag the points, use the mouse wheel or arrow keys. Double-click resets a band.":
        "Arrastra los puntos, usa la rueda del ratón o las flechas. Doble clic restablece una banda.",
    # microphone
    "Sidetone": "Tono lateral (sidetone)",
    "Hear your own voice in the headset": "Escucha tu propia voz en los auriculares",
    "Mic volume": "Volumen del micrófono",
    "Mute microphone": "Silenciar micrófono",
    "Voice enhancement": "Mejora de voz",
    "Software voice processing (Blue VO!CE style) creating a new virtual microphone":
        "Procesado de voz por software (estilo Blue VO!CE) que crea un nuevo micrófono virtual",
    "Use as default microphone": "Usar como micrófono predeterminado",
    "Noise reduction (WebRTC)": "Reducción de ruido (WebRTC)",
    "High-pass filter": "Filtro paso alto",
    "Noise gate": "Puerta de ruido",
    "Threshold": "Umbral",
    "Bass": "Graves",
    "Presence": "Presencia",
    "De-esser": "De-esser",
    "Air": "Brillo",
    "Output gain": "Ganancia de salida",
    "Voice preset": "Preajuste de voz",
    "Broadcast": "Locutor",
    "Natural": "Natural",
    "Clear & crisp": "Claro y nítido",
    "Warm": "Cálido",
    "Streamer": "Streamer",
    "Input level": "Nivel de entrada",
    "Microphone not available": "Micrófono no disponible",
    "Mic test": "Prueba de micro",
    "The headset is sending digital silence: check its mic mute button and that the boom microphone is fully plugged in.":
        "Los auriculares envían silencio digital: comprueba el botón de silencio del micro y que el brazo del "
        "micrófono esté bien insertado.",
    "Grant access": "Conceder acceso",
    "{name}: PipeWire could not start the filter": "{name}: PipeWire no pudo iniciar el filtro",
    "Could not install the udev rule": "No se pudo instalar la regla udev",
    # audio / surround
    "Headset volume": "Volumen de los auriculares",
    "Mute": "Silenciar",
    "Set as default output": "Usar como salida predeterminada",
    "Default output": "Salida predeterminada",
    "Default input": "Entrada predeterminada",
    "Virtual 7.1 surround": "Sonido envolvente 7.1 virtual",
    "HRTF binaural rendering of 7.1 content (DTS Headphone:X style), as a new audio output":
        "Renderizado binaural HRTF de contenido 7.1 (estilo DTS Headphone:X) como nueva salida de audio",
    "Use as default output": "Usar como salida predeterminada",
    "HRTF dataset": "Perfil HRTF",
    "Level": "Nivel",
    "Click a speaker to test it": "Haz clic en un altavoz para probarlo",
    "PipeWire is required for this feature": "Esta función requiere PipeWire",
    "No SOFA HRTF dataset found (install libmysofa)": "No se encontró ningún perfil HRTF SOFA (instala libmysofa)",
    "Headset audio device not found": "No se encontró el dispositivo de audio de los auriculares",
    # device info
    "Device information": "Información del dispositivo",
    "Name": "Nombre",
    "Model": "Modelo",
    "Firmware": "Firmware",
    "HID++ protocol": "Protocolo HID++",
    "USB ID": "ID USB",
    "Device node": "Nodo del dispositivo",
    "Unit ID": "ID de unidad",
    "Battery voltage": "Tensión de batería",
    "HID++ features": "Funciones HID++",
    "Battery": "Batería",
    # settings
    "Application": "Aplicación",
    "Start with the system": "Iniciar con el sistema",
    "Start minimized to tray": "Iniciar minimizado en la bandeja",
    "Close button minimizes to tray": "El botón cerrar minimiza a la bandeja",
    "Re-apply profile when the headset connects": "Reaplicar el perfil al conectar los auriculares",
    "Notifications": "Notificaciones",
    "Connection notifications": "Avisos de conexión",
    "Low battery warning": "Aviso de batería baja",
    "Warn at": "Avisar al",
    "Battery refresh interval": "Intervalo de lectura de batería",
    "Language": "Idioma",
    "Automatic": "Automático",
    "Restart the app to change the language": "Reinicia la aplicación para cambiar el idioma",
    "About": "Acerca de",
    "Open-source control panel for Logitech headsets on Linux.":
        "Panel de control de código abierto para auriculares Logitech en Linux.",
    "Not affiliated with Logitech. Product images © Logitech.":
        "Sin afiliación con Logitech. Imágenes del producto © Logitech.",
    # profiles
    "New profile…": "Nuevo perfil…",
    "Duplicate profile…": "Duplicar perfil…",
    "Rename profile…": "Renombrar perfil…",
    "Delete profile": "Eliminar perfil",
    "Profile name": "Nombre del perfil",
    "Delete profile “{name}”?": "¿Eliminar el perfil «{name}»?",
    # tray
    "Show": "Mostrar",
    "Quit": "Salir",
    "Battery: {p}%": "Batería: {p}%",
    "Headset connected": "Auriculares conectados",
    "Headset disconnected": "Auriculares desconectados",
    "Low battery": "Batería baja",
    "Battery at {p}%. Charge your headset soon.": "Batería al {p}%. Carga pronto tus auriculares.",
    "Off": "Apagado",
    "On": "Activado",
    "minutes": "minutos",
    "seconds": "segundos",
}

_lang = "es"


def set_language(lang: str) -> None:
    global _lang
    if lang == "auto":
        env = os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG") or ""
        lang = "es" if env.lower().startswith("es") else "en"
    _lang = lang


def tr(text: str, **kwargs) -> str:
    out = _ES.get(text, text) if _lang == "es" else text
    return out.format(**kwargs) if kwargs else out
