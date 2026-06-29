package com.h3.creative.queue.consumer;

import com.h3.creative.queue.message.BannerMessage;
import com.h3.creative.service.BannerService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.stereotype.Component;

@Slf4j
@Component
@RequiredArgsConstructor
public class BannerConsumer {

    private final BannerService bannerService;

    @RabbitListener(queues = "creative.banner.queue")
    public void consume(BannerMessage message) {
        log.info("Consuming banner job: {}", message.getJobId());
        bannerService.process(message);
    }
}
