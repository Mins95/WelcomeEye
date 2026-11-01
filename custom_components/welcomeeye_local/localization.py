"""Device presentation language; HA owns the language of its native forms."""
LANGUAGES = ('auto', 'fr', 'en', 'es')


def refresh_generated_output_names(registry, entries, unique_id):
    """Refresh old generated names, including inactive retained buttons.

    Only original_name changes. User names, entity IDs, options, disabled state
    and physical-output approvals are deliberately left to their owners.
    """
    replacements = {
        1: {'Portillon 2 (essai)': 'Portillon 2',
            'Strike 2 (trial)': 'Wicket gate 2',
            'Puerta peatonal 2 (prueba)': 'Puerta peatonal 2'},
        2: {'Portail 2 (essai)': 'Portail 2',
            'Gate 2 (trial)': 'Vehicle gate 2',
            'Portal 2 (prueba)': 'Portal 2'},
    }
    for item in entries:
        if item.domain != 'button' or item.platform != 'welcomeeye_local':
            continue
        for output, names in replacements.items():
            if item.unique_id != f'{unique_id}_open_channel_2_output_{output}':
                continue
            replacement = names.get(item.original_name)
            if replacement is not None:
                registry.async_update_entity(item.entity_id, original_name=replacement)


def configured_language(data):
    value = data.get('language', 'auto')
    return value if type(value) is str and value in LANGUAGES else 'auto'


def language_for(data, fallback='fr'):
    selected = configured_language(data)
    if selected != 'auto':
        return selected
    selected = str(fallback).split('-')[0].split('_')[0]
    return selected if selected in ('fr', 'en', 'es') else 'en'


OPENING_MESSAGES = {
    'fr': {
        'uncertain': 'La commande a pu être envoyée. Vérifiez sur place avant de réessayer.',
        'device_rejected': 'Le visiophone a refusé l’ouverture. Vérifiez le code d’ouverture dans Reconfigurer : il est distinct du mot de passe de connexion. Consultez les diagnostics pour le refus natif.',
        'device_busy': 'Le visiophone indique qu’il est occupé. Aucune nouvelle tentative automatique.',
        'opening_code_required': 'Renseignez le code d’ouverture dans Reconfigurer, distinct du mot de passe de connexion.',
        'output_not_enabled': 'Cette commande WelcomeEye est désactivée dans Reconfigurer.',
        'failed': 'Impossible de confirmer l’ouverture WelcomeEye ({error_type}).',
    },
    'en': {
        'uncertain': 'The command may have been sent. Check the device on site before trying again.',
        'device_rejected': 'The intercom rejected the opening command. Check the opening code in Reconfigure: it is separate from the connection password. See diagnostics for the native rejection.',
        'device_busy': 'The intercom reports that it is busy. There is no automatic retry.',
        'opening_code_required': 'Enter the opening code in Reconfigure, separate from the connection password.',
        'output_not_enabled': 'This WelcomeEye command is disabled in Reconfigure.',
        'failed': 'Unable to confirm the WelcomeEye opening command ({error_type}).',
    },
    'es': {
        'uncertain': 'La orden puede haberse enviado. Compruebe el dispositivo antes de volver a intentarlo.',
        'device_rejected': 'El videoportero ha rechazado la apertura. Compruebe el código de apertura en Reconfigurar: es distinto de la contraseña de conexión. Consulte el rechazo nativo en los diagnósticos.',
        'device_busy': 'El videoportero indica que está ocupado. No se volverá a intentar automáticamente.',
        'opening_code_required': 'Introduzca el código de apertura en Reconfigurar, distinto de la contraseña de conexión.',
        'output_not_enabled': 'Esta orden WelcomeEye está desactivada en Reconfigurar.',
        'failed': 'No se ha podido confirmar la apertura WelcomeEye ({error_type}).',
    },
}


def opening_error(hub, error):
    data = getattr(hub, 'entry', None)
    config = getattr(getattr(hub, 'hass', None), 'config', None)
    lang = language_for(getattr(data, 'data', {}), getattr(config, 'language', 'fr'))
    reason = 'uncertain' if getattr(error, 'physical_request_uncertain', False) else getattr(error, 'reason', 'failed')
    return OPENING_MESSAGES[lang].get(reason, OPENING_MESSAGES[lang]['failed']).format(
        error_type=type(error).__name__)
