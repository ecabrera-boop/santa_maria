from rest_framework.throttling import ScopedRateThrottle


class LoginRateThrottle(ScopedRateThrottle):
    scope = "eic_login"


class ClassifyRateThrottle(ScopedRateThrottle):
    scope = "eic_classify"
