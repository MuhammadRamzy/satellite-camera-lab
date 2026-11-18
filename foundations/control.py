"""Spacecraft authority guards shared by the ROS controller and host tests."""
SC_STANDBY, SC_OPERATIONAL, SC_SAFE, SC_COMMUNICATIONS = range(4)
BOOT, STANDBY, READY, DOWNLINK, SAFE, FAULT = range(6)


class SpacecraftAuthority:
    def __init__(self):
        self.mode = SC_STANDBY
        self.imaging_permitted = False

    def update(self, mode, imaging_permitted):
        if mode not in range(4):
            raise ValueError('unknown spacecraft mode')
        if imaging_permitted and mode != SC_OPERATIONAL:
            raise ValueError('imaging permission requires spacecraft OPERATIONAL')
        self.mode = mode
        self.imaging_permitted = bool(imaging_permitted)

    @property
    def may_image(self):
        return self.mode == SC_OPERATIONAL and self.imaging_permitted

    def guard_payload(self, target):
        if target not in (STANDBY, READY, DOWNLINK, SAFE):
            raise ValueError('payload mode is internal or unknown')
        if self.mode == SC_SAFE and target != SAFE:
            raise ValueError('spacecraft SAFE authority cannot be overridden locally')
        if target == READY and not self.may_image:
            raise ValueError('spacecraft imaging permission absent')
