import React, { useState } from 'react';
import { ImageOff } from 'lucide-react';

interface ImageWithFallbackProps extends React.ImgHTMLAttributes<HTMLImageElement> {
    fallbackSrc?: string;
    fallbackComponent?: React.ReactNode;
}

export const ImageWithFallback: React.FC<ImageWithFallbackProps> = ({
    src,
    alt,
    fallbackSrc,
    fallbackComponent,
    className,
    ...props
}) => {
    const [error, setError] = useState(false);

    if (error) {
        if (fallbackComponent) {
            return <>{fallbackComponent}</>;
        }
        if (fallbackSrc) {
            return <img src={fallbackSrc} alt={alt} className={className} {...props} />;
        }
        return (
            <div className={`flex items-center justify-center bg-[#18181b] text-gray-500 ${className}`}>
                <ImageOff className="w-1/2 h-1/2" />
            </div>
        );
    }

    return (
        <img
            src={src}
            alt={alt}
            className={className}
            onError={() => setError(true)}
            {...props}
        />
    );
};
