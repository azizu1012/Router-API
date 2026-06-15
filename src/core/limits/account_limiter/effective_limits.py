from typing import Any, Dict
from .capacity import (
    get_active_account_counts,
    calculate_key_capacities,
    calculate_key_capacities_by_pool,
)

async def get_effective_limits(account: Dict[str, Any]) -> tuple[int, int, int]:
    tier = account.get("tier", "free")
    
    active_counts = await get_active_account_counts()
    capacities = calculate_key_capacities()
    
    cfg_rpm = int(account.get("rpm") or 300)
    cfg_tpm = int(account.get("tpm") or 6000000)
    cfg_rpd = int(account.get("rpd") or 20000)
    
    if tier == "admin":
        cfg_rpm = 999999
        cfg_tpm = 999999999
        cfg_rpd = 999999
    elif tier == "premium":
        cfg_rpm = int(cfg_rpm * 1.5)
        cfg_tpm = int(cfg_tpm * 1.5)
        cfg_rpd = int(cfg_rpd * 1.5)
        
    Cap_free_rpm = capacities["free"]["rpm"]
    Cap_free_tpm = capacities["free"]["tpm"]
    Cap_free_rpd = capacities["free"]["rpd"]
    
    Cap_prem_rpm = capacities["premium"]["rpm"]
    Cap_prem_tpm = capacities["premium"]["tpm"]
    Cap_prem_rpd = capacities["premium"]["rpd"]
    
    Cap_admin_rpm = capacities["admin"]["rpm"]
    Cap_admin_tpm = capacities["admin"]["tpm"]
    Cap_admin_rpd = capacities["admin"]["rpd"]
    
    N_free = active_counts.get("free", 0)
    N_prem = active_counts.get("premium", 0)
    max(1, active_counts.get("admin", 0))
    
    # Compute RPM limits
    if N_free > 0 and N_prem > 0:
        total_weight = N_free + 1.5 * N_prem
        base_free_rpm = (Cap_free_rpm + Cap_prem_rpm) / total_weight
        l_free_rpm = min(base_free_rpm, Cap_free_rpm / N_free)
        l_prem_rpm = ((Cap_free_rpm + Cap_prem_rpm) - (N_free * l_free_rpm)) / N_prem
        if l_prem_rpm < 1.5 * l_free_rpm:
            l_free_rpm = (Cap_free_rpm + Cap_prem_rpm) / total_weight
            l_prem_rpm = 1.5 * l_free_rpm
    elif N_free > 0 and N_prem == 0:
        l_free_rpm = Cap_free_rpm / N_free
        l_prem_rpm = 0
    elif N_free == 0 and N_prem > 0:
        l_free_rpm = 0
        l_prem_rpm = (Cap_free_rpm + Cap_prem_rpm) / N_prem
    else:
        l_free_rpm = 0
        l_prem_rpm = 0
        
    # Compute TPM limits
    if N_free > 0 and N_prem > 0:
        total_weight = N_free + 1.5 * N_prem
        base_free_tpm = (Cap_free_tpm + Cap_prem_tpm) / total_weight
        l_free_tpm = min(base_free_tpm, Cap_free_tpm / N_free)
        l_prem_tpm = ((Cap_free_tpm + Cap_prem_tpm) - (N_free * l_free_tpm)) / N_prem
        if l_prem_tpm < 1.5 * l_free_tpm:
            l_free_tpm = (Cap_free_tpm + Cap_prem_tpm) / total_weight
            l_prem_tpm = 1.5 * l_free_tpm
    elif N_free > 0 and N_prem == 0:
        l_free_tpm = Cap_free_tpm / N_free
        l_prem_tpm = 0
    elif N_free == 0 and N_prem > 0:
        l_free_tpm = 0
        l_prem_tpm = (Cap_free_tpm + Cap_prem_tpm) / N_prem
    else:
        l_free_tpm = 0
        l_prem_tpm = 0
        
    # Compute RPD limits
    if N_free > 0 and N_prem > 0:
        total_weight = N_free + 1.5 * N_prem
        base_free_rpd = (Cap_free_rpd + Cap_prem_rpd) / total_weight
        l_free_rpd = min(base_free_rpd, Cap_free_rpd / N_free)
        l_prem_rpd = ((Cap_free_rpd + Cap_prem_rpd) - (N_free * l_free_rpd)) / N_prem
        if l_prem_rpd < 1.5 * l_free_rpd:
            l_free_rpd = (Cap_free_rpd + Cap_prem_rpd) / total_weight
            l_prem_rpd = 1.5 * l_free_rpd
    elif N_free > 0 and N_prem == 0:
        l_free_rpd = Cap_free_rpd / N_free
        l_prem_rpd = 0
    elif N_free == 0 and N_prem > 0:
        l_free_rpd = 0
        l_prem_rpd = (Cap_free_rpd + Cap_prem_rpd) / N_prem
    else:
        l_free_rpd = 0
        l_prem_rpd = 0
        
    # Compute Admin limits
    l_admin_rpm = Cap_free_rpm + Cap_prem_rpm + Cap_admin_rpm
    l_admin_tpm = Cap_free_tpm + Cap_prem_tpm + Cap_admin_tpm
    l_admin_rpd = Cap_free_rpd + Cap_prem_rpd + Cap_admin_rpd
    
    # Cap by configured limits
    if tier == "admin":
        eff_rpm = int(min(cfg_rpm, l_admin_rpm))
        eff_tpm = int(min(cfg_tpm, l_admin_tpm))
        eff_rpd = int(min(cfg_rpd, l_admin_rpd))
    elif tier == "premium":
        eff_rpm = int(min(cfg_rpm, l_prem_rpm))
        eff_tpm = int(min(cfg_tpm, l_prem_tpm))
        eff_rpd = int(min(cfg_rpd, l_prem_rpd))
    else:
        eff_rpm = int(min(cfg_rpm, l_free_rpm))
        eff_tpm = int(min(cfg_tpm, l_free_tpm))
        eff_rpd = int(min(cfg_rpd, l_free_rpd))
        
    return max(1, eff_rpm), max(1, eff_tpm), max(1, eff_rpd)

async def get_effective_limits_by_pool(account: Dict[str, Any], pool_type: str = "flash") -> tuple[int, int, int]:
    tier = account.get("tier", "free")
    
    cfg_rpm = int(account.get("rpm") or 300)
    cfg_tpm = int(account.get("tpm") or 6000000)
    cfg_rpd = int(account.get("rpd") or 20000)
    
    if pool_type == "custom":
        if tier == "admin":
            return 999999, 999999999, 999999
        elif tier == "premium":
            return int(cfg_rpm * 1.5), int(cfg_tpm * 1.5), int(cfg_rpd * 1.5)
        return cfg_rpm, cfg_tpm, cfg_rpd

    active_counts = await get_active_account_counts()
    capacities = calculate_key_capacities_by_pool(pool_type)
    
    cfg_rpm = int(account.get("rpm") or 300)
    cfg_tpm = int(account.get("tpm") or 6000000)
    cfg_rpd = int(account.get("rpd") or 20000)
    
    if tier == "admin":
        cfg_rpm = 999999
        cfg_tpm = 999999999
        cfg_rpd = 999999
    elif tier == "premium":
        cfg_rpm = int(cfg_rpm * 1.5)
        cfg_tpm = int(cfg_tpm * 1.5)
        cfg_rpd = int(cfg_rpd * 1.5)
        
    Cap_free_rpm = capacities["free"]["rpm"]
    Cap_free_tpm = capacities["free"]["tpm"]
    Cap_free_rpd = capacities["free"]["rpd"]
    
    Cap_prem_rpm = capacities["premium"]["rpm"]
    Cap_prem_tpm = capacities["premium"]["tpm"]
    Cap_prem_rpd = capacities["premium"]["rpd"]
    
    Cap_admin_rpm = capacities["admin"]["rpm"]
    Cap_admin_tpm = capacities["admin"]["tpm"]
    Cap_admin_rpd = capacities["admin"]["rpd"]
    
    N_free = active_counts.get("free", 0)
    N_prem = active_counts.get("premium", 0)
    max(1, active_counts.get("admin", 0))
    
    # Compute RPM limits
    if N_free > 0 and N_prem > 0:
        total_weight = N_free + 1.5 * N_prem
        base_free_rpm = (Cap_free_rpm + Cap_prem_rpm) / total_weight
        l_free_rpm = min(base_free_rpm, Cap_free_rpm / N_free)
        l_prem_rpm = ((Cap_free_rpm + Cap_prem_rpm) - (N_free * l_free_rpm)) / N_prem
        if l_prem_rpm < 1.5 * l_free_rpm:
            l_free_rpm = (Cap_free_rpm + Cap_prem_rpm) / total_weight
            l_prem_rpm = 1.5 * l_free_rpm
    elif N_free > 0 and N_prem == 0:
        l_free_rpm = Cap_free_rpm / N_free
        l_prem_rpm = 0
    elif N_free == 0 and N_prem > 0:
        l_free_rpm = 0
        l_prem_rpm = (Cap_free_rpm + Cap_prem_rpm) / N_prem
    else:
        l_free_rpm = 0
        l_prem_rpm = 0
        
    # Compute TPM limits
    if N_free > 0 and N_prem > 0:
        total_weight = N_free + 1.5 * N_prem
        base_free_tpm = (Cap_free_tpm + Cap_prem_tpm) / total_weight
        l_free_tpm = min(base_free_tpm, Cap_free_tpm / N_free)
        l_prem_tpm = ((Cap_free_tpm + Cap_prem_tpm) - (N_free * l_free_tpm)) / N_prem
        if l_prem_tpm < 1.5 * l_free_tpm:
            l_free_tpm = (Cap_free_tpm + Cap_prem_tpm) / total_weight
            l_prem_tpm = 1.5 * l_free_tpm
    elif N_free > 0 and N_prem == 0:
        l_free_tpm = Cap_free_tpm / N_free
        l_prem_tpm = 0
    elif N_free == 0 and N_prem > 0:
        l_free_tpm = 0
        l_prem_tpm = (Cap_free_tpm + Cap_prem_tpm) / N_prem
    else:
        l_free_tpm = 0
        l_prem_tpm = 0
        
    # Compute RPD limits
    if N_free > 0 and N_prem > 0:
        total_weight = N_free + 1.5 * N_prem
        base_free_rpd = (Cap_free_rpd + Cap_prem_rpd) / total_weight
        l_free_rpd = min(base_free_rpd, Cap_free_rpd / N_free)
        l_prem_rpd = ((Cap_free_rpd + Cap_prem_rpd) - (N_free * l_free_rpd)) / N_prem
        if l_prem_rpd < 1.5 * l_free_rpd:
            l_free_rpd = (Cap_free_rpd + Cap_prem_rpd) / total_weight
            l_prem_rpd = 1.5 * l_free_rpd
    elif N_free > 0 and N_prem == 0:
        l_free_rpd = Cap_free_rpd / N_free
        l_prem_rpd = 0
    elif N_free == 0 and N_prem > 0:
        l_free_rpd = 0
        l_prem_rpd = (Cap_free_rpd + Cap_prem_rpd) / N_prem
    else:
        l_free_rpd = 0
        l_prem_rpd = 0
        
    # Compute Admin limits
    l_admin_rpm = Cap_free_rpm + Cap_prem_rpm + Cap_admin_rpm
    l_admin_tpm = Cap_free_tpm + Cap_prem_tpm + Cap_admin_tpm
    l_admin_rpd = Cap_free_rpd + Cap_prem_rpd + Cap_admin_rpd
    
    # Cap by configured limits
    if tier == "admin":
        eff_rpm = int(min(cfg_rpm, l_admin_rpm))
        eff_tpm = int(min(cfg_tpm, l_admin_tpm))
        eff_rpd = int(min(cfg_rpd, l_admin_rpd))
    elif tier == "premium":
        eff_rpm = int(min(cfg_rpm, l_prem_rpm))
        eff_tpm = int(min(cfg_tpm, l_prem_tpm))
        eff_rpd = int(min(cfg_rpd, l_prem_rpd))
    else:
        eff_rpm = int(min(cfg_rpm, l_free_rpm))
        eff_tpm = int(min(cfg_tpm, l_free_tpm))
        eff_rpd = int(min(cfg_rpd, l_free_rpd))
        
    return max(1, eff_rpm), max(1, eff_tpm), max(1, eff_rpd)
