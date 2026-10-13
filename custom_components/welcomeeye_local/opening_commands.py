"""Persisted command preferences, separate from protocol/physical consent.

Reading an older entry is a lazy migration: primary outputs keep their former
availability, while secondary choices inherit only an explicit former trial.
No network, registry or configuration mutation happens here.
"""

from .connect3.channels import channel2_enabled

COMMAND_FIELDS = {
    'strike_1': 'strike_1_enabled',
    'gate_1': 'gate_1_enabled',
    'strike_2': 'strike_2_enabled',
    'gate_2': 'gate_2_enabled',
}
SECONDARY_CONSENTS = {
    'strike_2': 'channel2_strike_trial_enabled',
    'gate_2': 'channel2_gate_trial_enabled',
}


def command_preference(data, target):
    """Return an owner's choice, without claiming a protocol capability."""
    field = COMMAND_FIELDS.get(target)
    if field is None:
        return False
    if field in data:
        return data[field] is True
    consent = SECONDARY_CONSENTS.get(target)
    return data.get(consent) is True if consent else True


def command_enabled(data, target):
    """Apply preference and secondary opt-in; callers still check capabilities."""
    if not command_preference(data, target):
        return False
    consent = SECONDARY_CONSENTS.get(target)
    return not consent or (second_panel_enabled(data)
                           and data.get(consent) is True)


def second_panel_enabled(data):
    """Keep endpoint-bound Connect 3 observations; other families remain opt-in."""
    if data.get('protocol_family') == 'connect3_qv_experimental':
        return channel2_enabled(data)
    return data.get('second_channel_enabled') is True


def secondary_preferences(data):
    """Keep previous choices when revoking access to an absent second panel."""
    return {COMMAND_FIELDS[target]: command_preference(data, target)
            for target, consent in SECONDARY_CONSENTS.items()
            if COMMAND_FIELDS[target] in data or consent in data}
