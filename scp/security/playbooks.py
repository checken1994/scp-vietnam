from dataclasses import dataclass

ALLOWED_ACTIONS = {'block_ip', 'tighten_rate_limit', 'enable_honeypot', 'log_forensic', 'alert_human', 'alert_cert', 'canary_inject', 'quarantine', 'isolate', 'forensic_snapshot'}
FORBIDDEN_ACTIONS = {'hack_back', 'data_destruction', 'preemptive_strike', 'counter_attack', 'reverse_exploit'}

@dataclass
class Playbook:
    name: str
    severity: str
    actions: list[str]
    timeout_min: int
    requires_human_approval: bool

    def validate(self):
        for action in self.actions:
            if action in FORBIDDEN_ACTIONS:
                raise ValueError(f"Forbidden action '{action}' in playbook '{self.name}'")

class PlaybookRegistry:
    DEFAULT_PLAYBOOKS: dict[str, Playbook] = {
        'ddos_volumetric': Playbook(
            name='ddos_volumetric',
            severity='high',
            actions=['block_ip', 'tighten_rate_limit', 'alert_human'],
            timeout_min=0,
            requires_human_approval=False
        ),
        'prompt_injection': Playbook(
            name='prompt_injection',
            severity='medium',
            actions=['log_forensic', 'canary_inject', 'alert_human'],
            timeout_min=0,
            requires_human_approval=False
        ),
        'apt_predicted': Playbook(
            name='apt_predicted',
            severity='high',
            actions=['block_ip', 'enable_honeypot', 'forensic_snapshot'],
            timeout_min=30,
            requires_human_approval=True
        ),
        'supply_chain_attack': Playbook(
            name='supply_chain_attack',
            severity='critical',
            actions=['alert_human', 'quarantine'],
            timeout_min=1440,
            requires_human_approval=False
        ),
        'zero_day_exploit': Playbook(
            name='zero_day_exploit',
            severity='critical',
            actions=['isolate', 'alert_cert'],
            timeout_min=0,
            requires_human_approval=False
        )
    }
