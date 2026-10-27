import numpy as np

from rtlsdr import RtlSdr


async def read_iq_stream_async(
    center_freq=433.125e6,
    sample_rate=1.024e6,
    gain="auto",
    block_size=16384,
):
    sdr = RtlSdr()

    try:
        sdr.sample_rate = sample_rate
        sdr.center_freq = center_freq
        
        if isinstance(gain, str) and gain.lower() == "auto":
            sdr.gain = "auto"
            selected_gain = "auto"
        else:
            supported = sdr.valid_gains_db
            selected_gain = min(supported, key=lambda g: abs(g - float(gain)))
            sdr.set_gain(selected_gain)

        print("RTL-SDR initialized")
        print(
            f"Frequency: "
            f"{sdr.center_freq / 1e6:.6f} MHz"
        )
        print(
            f"Sample rate: "
            f"{sdr.sample_rate / 1e6:.3f} MS/s"
        )

        async for samples in sdr.stream():
            samples = np.asarray(
                samples,
                dtype=np.complex64,
            )  

            yield samples

    finally:
        print("Closing RTL-SDR...")

        try:
            await sdr.stop()
        except Exception:
            pass

        sdr.close()

        print("RTL-SDR closed")