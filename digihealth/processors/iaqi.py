from typing import Dict, Any, List

class IAQIProcessor:
    """Processes IAQI (Indoor Air Quality Index) calculations."""

    PM25_BREAKPOINTS = [
        {"C_lo": 0.0, "C_hi": 12.0, "I_lo": 0, "I_hi": 50},
        {"C_lo": 12.1, "C_hi": 35.4, "I_lo": 51, "I_hi": 100},
        {"C_lo": 35.5, "C_hi": 55.4, "I_lo": 101, "I_hi": 150},
        {"C_lo": 55.5, "C_hi": 150.0, "I_lo": 151, "I_hi": 200},
    ]

    CO2_BREAKPOINTS = [
        {"C_lo": 400, "C_hi": 800, "I_lo": 0, "I_hi": 50},
        {"C_lo": 801, "C_hi": 1000, "I_lo": 51, "I_hi": 100},
        {"C_lo": 1001, "C_hi": 1500, "I_lo": 101, "I_hi": 150},
        {"C_lo": 1501, "C_hi": 2000, "I_lo": 151, "I_hi": 200},
        {"C_lo": 2001, "C_hi": 5000, "I_lo": 201, "I_hi": 300},
    ]

    TVOC_BREAKPOINTS = [
        {"C_lo": 0, "C_hi": 100, "I_lo": 0, "I_hi": 50},
        {"C_lo": 101, "C_hi": 200, "I_lo": 51, "I_hi": 100},
        {"C_lo": 201, "C_hi": 300, "I_lo": 101, "I_hi": 150},
        {"C_lo": 301, "C_hi": 500, "I_lo": 151, "I_hi": 200},
    ]

    CH2O_BREAKPOINTS = [
        {"C_lo": 0, "C_hi": 50, "I_lo": 0, "I_hi": 50},
        {"C_lo": 51, "C_hi": 100, "I_lo": 51, "I_hi": 100},
        {"C_lo": 101, "C_hi": 200, "I_lo": 101, "I_hi": 150},
        {"C_lo": 201, "C_hi": 1000, "I_lo": 151, "I_hi": 200},
    ]

    NO2_BREAKPOINTS = [
        {"C_lo": 0.0, "C_hi": 0.05, "I_lo": 0, "I_hi": 50},
        {"C_lo": 0.051, "C_hi": 0.10, "I_lo": 51, "I_hi": 100},
        {"C_lo": 0.101, "C_hi": 0.20, "I_lo": 101, "I_hi": 150},
        {"C_lo": 0.201, "C_hi": 1.0, "I_lo": 151, "I_hi": 200},
    ]

    def process(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate IAQI and add to data."""
        pm25 = data.get("PM2_5-Particolato-[µg/m^3]", 0)
        co2 = data.get("CO2-AnidrideCarbonica-[ppm]", 0)
        tvoc = data.get("TVOC-QualitaAria-[G]", 0)
        ch2o = data.get("CH2O-Formaldeie-[mg/m^3]", 0)
        no2 = data.get("NO2-BiossidoDiAzoto-[ppm]", 0)

        iaq_pm25 = self._calculate_sub_index(pm25, self.PM25_BREAKPOINTS)
        iaq_co2 = self._calculate_sub_index(co2, self.CO2_BREAKPOINTS)
        iaq_tvoc = self._calculate_sub_index(tvoc, self.TVOC_BREAKPOINTS)
        iaq_ch2o = self._calculate_sub_index(ch2o, self.CH2O_BREAKPOINTS)
        iaq_no2 = self._calculate_sub_index(no2, self.NO2_BREAKPOINTS)

        iAQI = int(max(iaq_pm25, iaq_co2, iaq_ch2o, iaq_no2))

        data["IAQI"] = iAQI

        # Simplified dashboard data
        data["dashboard"] = {
            "temp": round(data.get("TEMP-[C]", 0), 1),
            "humidity": data.get("HUM-[%]", 0),
            "co2": data.get("CO2-AnidrideCarbonica-[ppm]", 0),
            "iaqi": iAQI
        }

        return data

    def _calculate_sub_index(self, C: float, breakpoints: List[Dict[str, float]]) -> float:
        """Calculate sub-index for a pollutant."""
        for bp in breakpoints:
            if bp["C_lo"] <= C <= bp["C_hi"]:
                return ((bp["I_hi"] - bp["I_lo"]) / (bp["C_hi"] - bp["C_lo"])) * (C - bp["C_lo"]) + bp["I_lo"]
        return breakpoints[-1]["I_hi"]