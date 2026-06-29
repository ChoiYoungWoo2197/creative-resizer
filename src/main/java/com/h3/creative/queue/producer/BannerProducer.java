package com.h3.creative.queue.producer;

import com.h3.creative.queue.message.BannerMessage;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.stereotype.Component;

@Slf4j
@Component
@RequiredArgsConstructor
public class BannerProducer {

    private static final String EXCHANGE = "creative.banner";
    private static final String ROUTING_KEY = "banner.generate";

    private final RabbitTemplate rabbitTemplate;

    public void publish(BannerMessage message) {
        log.info("Publishing banner job: {}", message.getJobId());
        rabbitTemplate.convertAndSend(EXCHANGE, ROUTING_KEY, message);
    }
}
