!!! Note ""
    Safety API is designed to provide safety features when working with the Spin board.

To work with Safety API, include the following file in your code:
!!! note
    ```
    #include <SafetyAPI.h>
    ```


## Quick start

Safety API watches shield sensors against a minimum and a maximum threshold. When a watched sensor stays out of its range, the reaction is applied to all the legs: `Open_Circuit` turns off both switches, `Short_Circuit` turns on the low-side switch.

The check runs at the start of each period of the critical task, so a [critical task](task_introduction.md) must be running. A fault must be seen on 5 consecutive periods before the reaction is applied.

!!! example
    ```cpp
    sensor_t watched[] = {V1_LOW, V2_LOW, I1_LOW, I2_LOW};
    float32_t max_values[] = {30, 30, 4, 4};
    float32_t min_values[] = {0, 0, -4, -4};

    safety.initShield(watched, 4);                          // Watch these sensors, default thresholds
    safety.setChannelThresholdMax(watched, max_values, 4);
    safety.setChannelThresholdMin(watched, min_values, 4);
    safety.setChannelReaction(Open_Circuit);
    safety.enableSafetyApi();
    ```

    Then, for example in the background task:
    ```cpp
    if (safety.getChannelError(I1_LOW))
    {
        /* I1_LOW went out of its range */
    }
    ```

Calling `safety.initShield()` without parameters watches all the shield sensors with their default thresholds. Thresholds can be kept across resets with `safety.storeThreshold()` and `safety.retrieveThreshold()`.

## API Reference
::: doxy.powerAPI.class
name: SafetyAPI
