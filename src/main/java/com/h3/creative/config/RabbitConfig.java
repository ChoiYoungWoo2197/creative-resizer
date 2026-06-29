package com.h3.creative.config;

import org.springframework.amqp.core.*;
import org.springframework.amqp.rabbit.connection.ConnectionFactory;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.amqp.support.converter.Jackson2JsonMessageConverter;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class RabbitConfig {

    public static final String EXCHANGE = "creative.banner";
    public static final String QUEUE = "creative.banner.queue";
    public static final String ROUTING_KEY = "banner.generate";

    @Bean
    public DirectExchange bannerExchange() {
        return new DirectExchange(EXCHANGE);
    }

    @Bean
    public Queue bannerQueue() {
        return QueueBuilder.durable(QUEUE).build();
    }

    @Bean
    public Binding bannerBinding(Queue bannerQueue, DirectExchange bannerExchange) {
        return BindingBuilder.bind(bannerQueue).to(bannerExchange).with(ROUTING_KEY);
    }

    @Bean
    public Jackson2JsonMessageConverter messageConverter() {
        return new Jackson2JsonMessageConverter();
    }

    @Bean
    public RabbitTemplate rabbitTemplate(ConnectionFactory connectionFactory) {
        RabbitTemplate template = new RabbitTemplate(connectionFactory);
        template.setMessageConverter(messageConverter());
        return template;
    }
}
