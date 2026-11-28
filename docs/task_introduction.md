!!! note ""
    PowerAPI is meant to deploy easily Real-time programs that must guarantee response within specified time constraints.
    In that context, scheduling is the action of asigning computing ressources to perform tasks.

## Include

!!! note
    ```
    #include <TaskAPI.h>
    ```
    Make sure that Task API is included

## Deploying a real time control task

Having a periodical code execution is key to real time applications. It is easy to spawn one using the TaskAPI.


### The control task sources

 You can have different **source** calling the control task.

The control task is synchronous, it means that it is called at fixed period. So we need some kind of timer calling the control, this timer is considered as the **source**. There are two sources :
- The PWM carrier
- An independant timer

#### The PWM carrier

The carrier has a period called the **switching period**, we can use it to call the control task after a fixed number of switching period.

![PWM control task](images/pwm_source_task.svg)

On the figure above, the switching period is 5µs (200Khz) and we call the control every 10 switching cycle so 50µs (20Khz).

!!! warning
    There are limitations when using this method :
        - You need to start the a PWM to start the control task
        - You can only have control period which are multiple of the switching period
        - You can not have a control period inferior to the switching period

!!! tip
    Synchronizing the control task period with PWM period can be usefull when you try to synchronize PWM between several SPIN or TWIST, in that case the control task is also synchronized between the board.

#### Independant timer

A simple timer not related to the PWM can be used to compute the control task period. We choose one of the MCU timer (the `timer 6`), to which we give our control period and this timer will call the control task each period.

![timer source](images/timer_source_task.svg)

!!! tip
    With an independant timer you can choose any value in µs as the control period, there is not the same limitation as the PWM source.

!!! warning
    The disavantage of such method is that since it is independant from the PWM you can't have synchronization between several control task modules.

### Initialization sequence

!!! note
    === "Periodic task based on PWM source"
        1\. Create the critical task and link it to the function to be called and choose hrtim as the source source. [`task.createCritical(critical_function, control_period, source_hrtim);`](https://owntech-foundation.github.io/Documentation/core/docs/scheduling/#function-createcritical)
        2\. Start the critical function.  [`task.startCritical()`](https://owntech-foundation.github.io/Documentation/core/docs/scheduling/#function-startcritical)

    === "Periodic task based on Timer6"
        1\. Create the critical task and link it to the function to be called and choose tim6 as the source.  [`task.createCritical(critical_function, control_period, source_tim6);`](https://owntech-foundation.github.io/Documentation/core/docs/scheduling/#function-createcritical)
        2\. Start the critical function.  [`task.startCritical()`](https://owntech-foundation.github.io/Documentation/core/docs/scheduling/#function-startcritical)

!!! example
    === "20kHz Periodic task based on PWM"
        ```
            task.createCritical(my_critical_function, 50, source_hrtim);
            task.startCritical();
        ```
    === "10kHz Periodic task based on Timer6"
        ```
            task.createCritical(my_critical_function, 100, source_tim6);
            task.startCritical();
        ```

!!! danger "The critical task is a zero-latency interrupt: no kernel calls inside"
    The critical task runs in an interrupt above everything the operating system (Zephyr) schedules. With
    `source_tim6` it is a *zero-latency* interrupt: the kernel never masks it, so it keeps its timing even while the
    kernel works on its own data, and the kernel cannot protect that data from the task. With `source_hrtim` it is
    the highest-priority interrupt the kernel manages (it waits while the kernel masks interrupts). Write every
    critical task as a zero-latency one, whatever its source, so it can move from one source to the other:
    **never call the kernel from a critical task**. No `printk`, `k_uptime_get()`, `k_sleep()`, `k_malloc()`,
    mutexes, semaphores or message queues, and none of `task.suspendBackgroundMs()` / `task.suspendBackgroundUs()`.
    Such a call can corrupt the kernel or fault the board, sometimes long after it was written.

    What a critical task may call: the PWM and data hot-path calls (`spin.pwm.setDutyCycle()`,
    `spin.data.getLatestValue()`, `spin.data.getValues()`, `spin.data.peekLatestValue()`, `spin.data.convertValue()`,
    ...), GPIO and DAC writes, and plain computation. To log or time something, write it to a variable in the
    critical task and print it or stamp it from a background task.

!!! tip
    Having a control Task is required for [synchronous measurements](spin_dataAPI.md/#synchronous-with-pwms) to work correctly.

## Non time critical tasks

In the powerAPI, non time critical tasks are refered as background tasks.

### Priority between critical and non-critical task

The control task has priority over any other task. It will preempts any background task. The control task can not be preempted. That is why it is also refered as an uninteruptible task.

![task priority](images/task_priority.svg)

### Pseudo periodicity of non-critical task

Non-critical tasks aren't synchronous, meaning they're not recurring at regular intervals. Instead, they operate in a pseudo-periodic manner: we can temporarily halt them for a specific duration, during which they remain inactive.  Below, in the illustration we suspend the background task for a period of 500ms. After 500ms we can execute again the background task, but we need to end the control task to do that (the critical task is uninteruptible).

![](images/task_pseudo_periodic.svg)


### Initialization sequence

!!! note
    1\. Create the background task and link it to the function to be called. [`task.createBackground(function)`](https://owntech-foundation.github.io/Documentation/core/docs/scheduling/#function-createbackground)
    2\. Start the background function. [`task.startBackground(task_number)`](https://owntech-foundation.github.io/Documentation/core/docs/scheduling/#function-startbackground)

!!! example
    === "Spawning a background task"
        ```
            void my_background_function(){
                do_stuff();
            }
            uint8_t task_number = task.createBackground(my_background_function);
            task.startBackground(task_number);
        ```
        In that case `do_stuff()` will execute continuously each time the processor is not occupied by the critical task.

    === "Making it pseudo periodic"
        ```
            void my_background_function(){
                do_stuff();
                task.suspendBackgroundUs(500);
            }
        ```
        In that case after executing `do_stuff();` the task will be suspended for 500us and resumed after. It creates a pseudo periodical task, runs every 500us + the time taken to execute `do_stuff()`.


::: doxy.powerAPI.class
name: TaskAPI
