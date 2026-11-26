!!! Note ""
    Communication API is used to interact with the Spin board and its shields communication peripherals.

To work with Communication API, include the following file in your code:
!!! note
    ```
    #include <CommunicationAPI.h>
    ```

Communication API is experimental and disabled by default: enable it with `CONFIG_OWNTECH_COMMUNICATION=y` in `zephyr/prj.conf`. Analog, RS485 and synchronization are then enabled by default; CAN needs `CONFIG_OWNTECH_COMMUNICATION_ENABLE_CAN=y`. A `communication` object gives access to each bus.

!!! example
    === "Analog"
        ```cpp
        communication.analog.init();                    // Set up the analog bus (ADC and DAC)
        communication.analog.setAnalogCommValue(2048);  // Drive the bus, value from 0 to 4096
        float32_t bus_value = communication.analog.getAnalogCommValue(); // Read the bus
        ```

    === "RS485"
        ```cpp
        #define DATA_SIZE 4
        uint8_t tx_buffer[DATA_SIZE];
        uint8_t rx_buffer[DATA_SIZE];

        void on_reception()
        {
            /* rx_buffer holds the received bytes */
        }

        communication.rs485.configure(tx_buffer, rx_buffer, DATA_SIZE,
                                      on_reception, SPEED_10M);
        communication.rs485.startTransmission(); // Send the content of tx_buffer
        ```

    === "Real-time synch"
        ```cpp
        communication.sync.initMaster(); // On the board that sends the synchronization pulse
        communication.sync.initSlave();  // On each board that receives it
        ```

    === "CAN"
        ```cpp
        communication.can.setCanNodeAddr(2);       // Node address on the bus
        communication.can.setBroadcastPeriod(1);   // Broadcast every second
        communication.can.setBroadcastEnable(true);
        ```

## Detailed documentation on available classes:
- [Analog communication](https://owntech-foundation.github.io/Documentation/powerAPI/classAnalogCommunication)
- [RS485](https://owntech-foundation.github.io/Documentation/powerAPI/classRs485Communication)
- [CAN](https://owntech-foundation.github.io/Documentation/powerAPI/classCanCommunication)
- [Real-Time synch](https://owntech-foundation.github.io/Documentation/powerAPI/classSyncCommunication)

## API Reference
::: doxy.powerAPI.class
name: CommunicationAPI
